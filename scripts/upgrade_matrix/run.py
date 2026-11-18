"""Run the local, offline stored-data matrix against pinned release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter
from pathlib import Path

from crash_driver import start_worker

RELEASES = (
    "0.1.0-alpha",
    "0.1.0-alpha.1",
    "0.1.0-alpha.2",
    "0.1.0-alpha.3",
    "0.1.0-beta",
    "0.2.1",
    "0.3.0",
    "0.4.0",
    "0.4.1",
    "0.5.0",
    "0.6.0",
    "0.7.0-rc.3",
    "0.7.0-rc.5",
    "main",
)
JOURNAL_RELEASES = {
    "T": ("0.2.1", "0.3.0", "0.4.0", "0.4.1", "0.5.0", "0.6.0", "0.7.0-rc.3", "0.7.0-rc.5", "main"),
    "I": ("0.5.0", "0.6.0", "0.7.0-rc.3", "0.7.0-rc.5", "main"),
    "D": ("0.6.0", "0.7.0-rc.3", "0.7.0-rc.5", "main"),
}
PHASES = {
    "T": ("preparing", "writing", "afterFirstWrite", "committed"),
    "I": ("preparing", "publishing", "published", "rollback"),
    "D": ("active", "gameBackedUp", "photoBackedUp", "committed"),
}
DECLINED_JOURNALS = {"T": ("outside", "missing"), "D": ("outside", "missing")}
RC_RELEASES = ("0.7.0-rc.3", "0.7.0-rc.5", "main")
MANIFESTS = {"library.json", "data.json"}
LOCAL_GIT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "file"}


def _offline(*command: str) -> list[str]:
    # Tagged code runs with a scratch HOME; the real user's application data stays unwritable even if a release
    # resolves a folder some other way.
    import pwd

    support = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "Application Support"
    profile = f'(version 1) (allow default) (deny network*) (deny file-write* (subpath "{support}"))'
    return ["/usr/bin/sandbox-exec", "-p", profile, *command]


def _uv_executable(repo: Path, override: Path | None) -> Path:
    name = str(override.expanduser()) if override is not None else shutil.which("uv")
    if not name:
        raise RuntimeError("Supply the uv executable with --uv or put it on PATH")
    candidate = Path(name).absolute()
    executable = candidate.resolve()
    if candidate.parent.name == "shims" or executable.name == "mise":
        mise = shutil.which("mise")
        if mise is None:
            raise RuntimeError("Cannot resolve the mise uv shim. Supply the real executable with --uv")
        found = subprocess.check_output([mise, "which", "uv"], cwd=repo, text=True, timeout=10).strip()
        executable = Path(found).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"uv is not executable: {executable}")
    version = subprocess.check_output([str(executable), "--version"], cwd=repo, text=True, timeout=10)
    if not version.startswith("uv "):
        raise RuntimeError(f"The executable did not report a uv version: {executable}")
    return executable


def _archive(repo: Path, ref: str, source: Path) -> str:
    env = {**os.environ, **LOCAL_GIT}
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo, env=env, text=True
    ).strip()
    with subprocess.Popen(["git", "archive", commit], cwd=repo, env=env, stdout=subprocess.PIPE) as process:
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
                tar.extractall(source, filter="data")
        except BaseException:
            process.kill()
            raise
        if process.wait():
            raise RuntimeError(f"Could not archive {ref}")
    return commit


def _environment(case: Path, cache: Path) -> dict:
    profile, temp = case / "home", case / "tmp"
    for path in (profile, temp):
        path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTHONPATH", "BAMT_TEST_GAME_DIR", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(key, None)
    env.update(
        {
            **LOCAL_GIT,
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_CACHE_DIR": str(cache),
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "APPDATA": str(profile / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(profile / "AppData" / "Local"),
            "XDG_CACHE_HOME": str(profile / ".cache"),
            "XDG_CONFIG_HOME": str(profile / ".config"),
            "TMPDIR": str(temp),
            "TEMP": str(temp),
            "TMP": str(temp),
            "BAMT_DATA_DIR": str(case / "data"),
            "BAMT_GAME_DIRS_GLOBAL": "",
            "BAMT_GAME_DIRS_JAPAN": "",
            "BAMT_DEV_ROOTS": str(case),
            "BAMT_DEV_WRITABLE": str(case),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _worker(source: Path, case: Path, cache: Path, release: str, operation: str, *arguments: str) -> dict:
    python = source / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = Path(__file__).with_name("legacy_driver.py").resolve()
    result = subprocess.run(
        _offline(str(python), str(script), operation, release, str(case), *arguments),
        cwd=source,
        env=_environment(case, cache),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode not in (0, 91):
        raise RuntimeError(f"{release} {operation} failed:\n{result.stdout}{result.stderr}")
    for line in reversed(result.stdout.splitlines()):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            return message
    raise RuntimeError(f"{release} {operation} produced no fixture receipt")


def _hashes(root: Path, *, include_private: bool) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.name.endswith(".lock")
        and (include_private or (path.name not in MANIFESTS and ".bmpt-upgrade" not in path.relative_to(root).parts))
    }


def _active(data: Path, library: Path) -> dict:
    result = {}
    for owner, root in (("data", data), ("library", library)):
        root_manifest = root / f"{owner}.json"
        result[owner] = {
            "files": _hashes(root / ".bmpt-upgrade", include_private=True),
            "manifest": root_manifest.read_bytes(),
        }
    return result


def _old_only(source: Path, case: Path, cache: Path, release: str, action: str) -> tuple[dict, dict]:
    # Both runs see the identical fixture paths and bytes. Keep the seed separate from the old writer.
    seed = case.with_name(f"{case.name}-seed")
    observed = case.with_name(f"{case.name}-old-only")
    shutil.copytree(case, seed, copy_function=shutil.copy2)
    try:
        result = _worker(source, case, cache, release, action)
        game = _hashes(case / "work" / "game", include_private=False)
    finally:
        case.rename(observed)
        shutil.copytree(seed, case, copy_function=shutil.copy2)
    return result, game


def _mutate(case: Path, seed: dict, change: str) -> None:
    if not change:
        return
    journal = Path(seed["journal"])
    if not journal.resolve().is_relative_to(case):
        raise RuntimeError("The tagged writer reported a journal outside its case")
    document = json.loads(journal.read_bytes())
    target_key = "files" if seed["legacyBoundary"].startswith("T:") else "games"
    entry = document[target_key][0]
    target = Path(entry["target"])
    if not target.resolve().is_relative_to(case / "work" / "game"):
        raise RuntimeError("The journal target escapes the fabricated game")
    if change == "outside":
        target.write_bytes(b"Outside change")
    elif change == "missing":
        target.unlink()
    elif change == "restored":
        original = case / "work" / "library" / entry["rollback"]
        if not original.resolve().is_relative_to(case):
            raise RuntimeError("The rollback payload escapes its fixture")
        shutil.copyfile(original, target)
    else:
        raise RuntimeError(f"Unknown recovery input mutation {change}")


def _case(
    source: Path,
    case: Path,
    cache: Path,
    release: str,
    *,
    interrupt: str = "",
    mutation: str = "",
) -> dict:
    case.mkdir()
    seed = _worker(source, case, cache, release, "seed", *(("--interrupt", interrupt) if interrupt else ()))
    library, game, data = case / "work" / "library", case / "work" / "game", case / "data"
    _mutate(case, seed, mutation)
    before = {root: _hashes(root, include_private=False) for root in (data, library, game)}
    action = "recover" if interrupt else "downgrade"
    control, control_game = _old_only(source, case, cache, release, action)
    upgraded = start_worker(data)
    store = interrupt.partition(":")[0]
    expected_pending = (
        store == "T"
        and mutation in DECLINED_JOURNALS["T"]
        or store == "D"
        and mutation in DECLINED_JOURNALS["D"]
        and release not in RC_RELEASES
    )
    if expected_pending:
        if upgraded["reason"] != "recoveryPending":
            raise RuntimeError(f"{release} {interrupt} {mutation} must preserve pending recovery: {upgraded}")
    elif upgraded["status"] != "ready":
        log = Path(upgraded.get("logFolder") or "") / "data-upgrade.log"
        tail = log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.is_file() else ""
        raise RuntimeError(f"Upgrade did not open {release} {interrupt}: {upgraded}\n{tail}")
    if not interrupt:
        if upgraded.get("notice") != "upgraded":
            raise RuntimeError(f"{release} did not offer the completed upgrade notice: {upgraded}")
        acknowledged = start_worker(data, action="acknowledge")
        if acknowledged["status"] != "ready" or acknowledged.get("notice") is not None:
            raise RuntimeError(f"{release} did not acknowledge the completed upgrade notice: {acknowledged}")
    if any(_hashes(root, include_private=False) != saved for root, saved in before.items()):
        raise RuntimeError(f"Upgrade modified legacy files or the game for {release} {interrupt}")
    private_before = _active(data, library)
    observed = _worker(source, case, cache, release, action)
    if observed != control or _hashes(game, include_private=False) != control_game:
        raise RuntimeError(f"{release} {interrupt} differs from its old-only control: {observed} vs {control}")
    if _active(data, library) != private_before:
        raise RuntimeError(f"The tagged writer modified generation-2 files for {release} {interrupt}")
    if not interrupt:
        changed = start_worker(data)
        kept = start_worker(data, action="keep")
        if changed["status"] != "changed" or kept["status"] != "ready" or kept.get("notice") is not None:
            raise RuntimeError(f"Could not acknowledge legacy edits for {release}: {changed}")
        return {"legacy": seed, "oldOnlyControl": True, "downgraded": observed, "status": "ready"}
    if expected_pending:
        # The App intentionally keeps evidence even when an RC would skip an outside change.
        return {
            "interrupt": interrupt,
            "mutation": mutation,
            "old": control["recovery"],
            "new": "recoveryPending",
            "oldOnlyControl": True,
        }
    seed_root = case.with_name(f"{case.name}-seed")
    for root in (data, library, game):
        staged_legacy = seed_root / root.relative_to(case)
        for path in tuple(root.iterdir()):
            if path.name in MANIFESTS or path.name == ".bmpt-upgrade":
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        shutil.copytree(staged_legacy, root, dirs_exist_ok=True, copy_function=shutil.copy2)
    recovered = start_worker(data, action="recover")
    if recovered.get("recovery") != control["recovery"]:
        raise RuntimeError(f"{release} {interrupt} recovery differs from its tagged implementation: {recovered}")
    if _hashes(game, include_private=False) != control_game:
        raise RuntimeError(f"{release} {interrupt} generation-2 recovery changed different game bytes")
    return {
        "interrupt": interrupt,
        "mutation": mutation,
        "old": control["recovery"],
        "new": recovered["recovery"],
        "oldOnlyControl": True,
    }


def _recovery_cases(release: str):
    for store, releases in JOURNAL_RELEASES.items():
        if release not in releases:
            continue
        for phase in PHASES[store]:
            yield f"{store}:{phase}", ""
        if store == "T":
            for mutation in ("outside", "missing", "restored"):
                yield "T:afterFirstWrite", mutation
        elif store == "D":
            for mutation in ("outside", "missing"):
                yield "D:gameBackedUp", mutation
        elif release in RC_RELEASES:
            for phase in ("copying", "replacing"):
                yield f"I:{phase}", ""


def _release(
    repo: Path, scratch: Path, cache: Path, interpreter: Path, uv: Path, release: str, interrupts: bool
) -> dict:
    source = scratch / "source"
    source.mkdir(parents=True)
    ref = "refs/heads/main" if release == "main" else f"refs/tags/{release}"
    commit = _archive(repo, ref, source)
    report = {"release": release, "commit": commit}
    env = _environment(scratch / "install", cache)
    result = subprocess.run(
        _offline(str(uv), "sync", "--offline", "--frozen", "--python", str(interpreter)),
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        detail = result.stderr or result.stdout
        if "sandbox_apply:" in detail:
            return {**report, "status": "failed", "sandboxError": detail.strip()}
        return {**report, "status": "offlineDependencyUnavailable", "dependencyError": detail}
    try:
        report.update(_case(source, scratch / "baseline", cache, release))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(status="failed", error=str(error))
    if interrupts:
        results = []
        for interrupt, mutation in _recovery_cases(release):
            folder = "-".join((interrupt.replace(":", "-"), mutation or "intact"))
            try:
                results.append(_case(source, scratch / folder, cache, release, interrupt=interrupt, mutation=mutation))
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                results.append({"interrupt": interrupt, "mutation": mutation, "error": str(error)})
        report["interruptions"] = results
        if any("error" in item for item in results):
            report["status"] = "failed"
    return report


def main() -> None:
    """Run selected local releases and emit exact commits, timings and offline setup failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", choices=RELEASES, action="append")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--uv", type=Path, help="Path to the real uv executable")
    parser.add_argument("--uv-cache", type=Path, required=True)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--interruptions", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("The release recovery matrix requires macOS and a local APFS scratch folder")
    cache = args.uv_cache.expanduser().resolve()
    interpreter = args.python.expanduser().absolute()
    if not cache.is_dir() or not interpreter.is_file():
        parser.error("Supply an existing offline uv cache and local Python interpreter")
    repo = Path(__file__).resolve().parents[2]
    try:
        uv = _uv_executable(repo, args.uv)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    counts = Counter(ready=0, failed=0)
    with tempfile.TemporaryDirectory(prefix="bmpt-upgrade-matrix-", dir=args.scratch) as folder:
        from bmpt.host.upgrade_files import volume

        scratch = Path(folder).resolve()
        if volume(scratch) != "apfs":
            parser.error("The release recovery matrix requires one local APFS volume")
        for release in args.tag or RELEASES:
            started = time.monotonic()
            try:
                report = _release(repo, scratch / release, cache, interpreter, uv, release, args.interruptions)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                report = {"release": release, "status": "failed", "error": str(error)}
            report["seconds"] = round(time.monotonic() - started, 2)
            counts[report["status"]] += 1
            print(json.dumps(report, sort_keys=True), flush=True)
            if "sandboxError" in report:
                break
    print(json.dumps({"summary": dict(counts)}, sort_keys=True), flush=True)
    raise SystemExit(0 if counts["ready"] == counts.total() else 1)


if __name__ == "__main__":
    main()
