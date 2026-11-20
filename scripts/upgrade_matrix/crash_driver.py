"""Exercise upgrade publication by killing a worker at a named boundary on APFS."""

from __future__ import annotations

import argparse
import json
import os
import queue
import runpy
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path

BOUNDARIES = (
    "discovery",
    "locking",
    "snapshot",
    "staging",
    "write:U",
    "seal:data",
    "seal:library",
    "rescan",
    "epoch:data:renamed",
    "epoch:library:renamed",
    "route:durable",
    "head:beforeReplace",
    "head:durable",
    "open:ready",
)


def _worker(data: Path, boundary: str, action: str, target: Path | None) -> int:
    from bmpt.host.data_upgrade import DataUpgrade

    def pause(label: str) -> None:
        if label == boundary:
            print(json.dumps({"paused": label}), flush=True)
            sys.stdin.buffer.read(1)

    upgrade = DataUpgrade(data, fault=pause)
    try:
        state = upgrade.run()
        if action in ("keep", "import") and state["status"] == "changed":
            state = upgrade.run(action)
        if state["reason"] == "olderVersion":
            state = upgrade.run()
        if action == "relocate" and state["status"] == "ready" and target is not None:
            upgrade.relocate(target)
        if state["status"] == "ready" and action == "recover":
            state.update(_recover(upgrade))
        if state["status"] == "ready" and action == "acknowledge":
            upgrade.acknowledge()
            state = upgrade.state()
        if upgrade.paths is not None:
            state["selectedLibrary"] = str(upgrade.paths.library_root)
        print(json.dumps(state), flush=True)
        return 0 if state["status"] == "ready" else 1
    finally:
        upgrade.close()


def _recover(upgrade) -> dict:
    from pytest import MonkeyPatch

    from bmpt.core.errors import ServiceError
    from bmpt.host.config import Settings
    from bmpt.library import ModLibrary, safety

    paths = upgrade.paths
    tests = Path(__file__).resolve().parents[2] / "tests"
    sys.path.insert(0, str(tests))
    fixture = runpy.run_path(str(tests / "test_library.py"))
    helpers = runpy.run_path(str(tests / "conftest.py"))
    catalog = fixture["FakeCatalog"](
        fixture["FakeAlt"]("test", "test_spr", "test_home_gl"),
        fixture["FakeAlt"]("other", "other_spr", student="other"),
    )
    with MonkeyPatch.context() as patch:
        helpers["header_only_bundles"].__wrapped__(patch)
        patch.setattr(safety, "check_game_not_running", lambda *_args, **_kwargs: None)
        try:
            library = ModLibrary(
                paths.library_epoch,
                catalog,
                object(),
                Settings(paths.config),
                game_write_gate=upgrade.game_write,
                catalog_write_gate=upgrade.catalog_write,
            )
            library._recover_pending()
        except ServiceError as error:
            return {"recovery": "blocked", "code": error.code}
    pending = sorted(
        str(path.relative_to(paths.library_epoch)) for path in paths.library_epoch.glob("*/*/journal.json")
    )
    return {"recovery": "deferred" if pending else "settled", "pending": pending}


def start_worker(
    data: Path,
    boundary: str = "",
    action: str = "retry",
    *,
    timeout: float = 60,
    target: Path | None = None,
) -> dict:
    """Run the isolated coordinator worker or kill it when its requested event is emitted."""
    driver = Path(__file__).resolve()
    profile = data.parent / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "BAMT_DATA_DIR": str(data),
        "BAMT_GAME_DIRS_GLOBAL": "",
        "BAMT_GAME_DIRS_JAPAN": "",
        "BAMT_DEV_ROOTS": str(data.parent),
        "BAMT_DEV_WRITABLE": str(data.parent),
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "LOCALAPPDATA": str(profile),
        "APPDATA": str(profile),
        "XDG_CACHE_HOME": str(profile / ".cache"),
        "TMPDIR": str(profile),
        "TEMP": str(profile),
        "TMP": str(profile),
    }
    command = [sys.executable, str(driver), "--worker", str(data), "--boundary", boundary, "--action", action]
    if target is not None:
        command.extend(("--target", str(target)))
    with subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        output = queue.Queue()

        def lines() -> None:
            for line in process.stdout:
                output.put(line)
            output.put(None)

        threading.Thread(target=lines, daemon=True).start()
        diagnostic = []
        try:
            while (line := output.get(timeout=timeout)) is not None:
                try:
                    message = json.loads(line)
                except ValueError:
                    diagnostic.append(line.rstrip())
                    continue
                if message.get("paused") == boundary:
                    process.kill()
                    process.wait(timeout=10)
                    return {"killed": boundary, "exit": process.returncode}
                if "status" in message:
                    process.wait(timeout=10)
                    if boundary:
                        raise RuntimeError(f"Worker did not reach {boundary}: {message}")
                    return message
            raise RuntimeError(f"Worker exited before reporting: {' '.join(diagnostic)}")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def _exercise(root: Path, boundaries: Iterable[str]) -> list[dict]:
    from bmpt.host.upgrade_files import volume

    if sys.platform != "darwin" or volume(root) != "apfs":
        raise RuntimeError("The process-kill durability driver needs a local APFS scratch directory")
    reports = []
    for number, boundary in enumerate(boundaries):
        data = root / str(number) / "data"
        data.mkdir(parents=True)
        state = data / "webui.json"
        legacy = b'{"prefs":{"data_id":"0123456789ab","marker":"variant:literal"}}\n'
        state.write_bytes(legacy)
        killed = start_worker(data, boundary)
        head = data / "library" / "library.json"
        expected = boundary in {"head:durable", "open:ready"}
        if head.exists() != expected or state.read_bytes() != legacy:
            raise RuntimeError(f"Incorrect publication state at {boundary}")
        resumed = start_worker(data)
        if resumed["status"] != "ready" or resumed.get("notice") != "upgraded" or state.read_bytes() != legacy:
            raise RuntimeError(f"Incorrect recovery from {boundary}: {resumed}")
        if boundary == "open:ready":
            acknowledged = start_worker(data, action="acknowledge")
            reopened = start_worker(data)
            if any(
                result["status"] != "ready" or result.get("notice") is not None for result in (acknowledged, reopened)
            ):
                raise RuntimeError("The completed upgrade notice returned after acknowledgement")
        reports.append({**killed, "headAtKill": expected, "restart": resumed["status"]})
    return reports


def _exercise_replacements(root: Path) -> list[dict]:
    results = []
    for boundary in ("head:previous", "head:beforeReplace", "head:durable"):
        data = root / f"import-{boundary.replace(':', '-')}" / "data"
        data.mkdir(parents=True)
        legacy = data / "webui.json"
        legacy.write_text('{"prefs":{"data_id":"0123456789ab","custom":1}}', encoding="utf-8")
        if start_worker(data)["status"] != "ready":
            raise RuntimeError("Could not seed the current generation before re-import")
        updated = b'{"prefs":{"data_id":"0123456789ab","custom":2}}'
        legacy.write_bytes(updated)
        result = start_worker(data, boundary, "import")
        expected = "ready" if boundary == "head:durable" else "changed"
        if start_worker(data)["status"] != expected or legacy.read_bytes() != updated:
            raise RuntimeError(f"Incorrect re-import recovery at {boundary}")
        results.append({"operation": "import", **result, "restart": expected})
    for boundary in ("relocation:beforeRoute", "relocation:route"):
        data = root / f"move-{boundary.replace(':', '-')}" / "data"
        data.mkdir(parents=True)
        target = data.parent / "target"
        if start_worker(data)["status"] != "ready":
            raise RuntimeError("Could not seed a source library before relocation")
        result = start_worker(data, boundary, "relocate", target=target)
        expected = target if boundary == "relocation:route" else data / "library"
        reopened = start_worker(data)
        if reopened["status"] != "ready" or reopened.get("selectedLibrary") != str(expected):
            raise RuntimeError(f"Incorrect relocation recovery at {boundary}: {reopened}")
        results.append({"operation": "relocate", **result, "restart": "ready"})
    return results


def main() -> None:
    """Run isolated publication cases or one subprocess worker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--boundary", default="")
    parser.add_argument("--action", default="retry")
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(_worker(args.worker, args.boundary, args.action, args.target))
    with tempfile.TemporaryDirectory(prefix="bmpt-upgrade-crash-", dir=args.scratch) as folder:
        root = Path(folder).resolve()
        reports = _exercise(root, (args.boundary,) if args.boundary else BOUNDARIES)
        if not args.boundary:
            reports.extend(_exercise_replacements(root))
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
