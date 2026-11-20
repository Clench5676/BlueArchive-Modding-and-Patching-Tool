"""Run the stored-data matrix's drivers against the current source, so a rename they reach by name fails here."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DRIVERS = REPO / "scripts" / "upgrade_matrix"
_spec = importlib.util.spec_from_file_location("upgrade_matrix_environment", DRIVERS / "environment.py")
_environment = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_environment)


def _apfs(path: Path) -> bool:
    from bmpt.host.upgrade_files import volume

    return sys.platform == "darwin" and volume(path) == "apfs"


def _run(case: Path, command: list[str], codes: tuple[int, ...] = (0,)) -> dict:
    env = _environment.case_environment(case, case / "uv-cache")
    # The drivers read the source under test, whichever checkout the interpreter was installed from.
    env["PYTHONPATH"] = str(REPO / "src")
    result = subprocess.run(
        [sys.executable, *command], cwd=REPO, env=env, capture_output=True, text=True, timeout=180, check=False
    )
    assert result.returncode in codes, f"{command[:2]} exited {result.returncode}:\n{result.stdout}{result.stderr}"
    for line in reversed(result.stdout.splitlines()):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            return message
    raise AssertionError(f"{command[:2]} printed no result:\n{result.stdout}{result.stderr}")


def _legacy(case: Path, operation: str, *arguments: str, codes: tuple[int, ...] = (0,)) -> dict:
    return _run(case, [str(DRIVERS / "legacy_driver.py"), operation, "main", str(case), *arguments], codes)


def _current(case: Path, action: str = "retry") -> dict:
    command = [str(DRIVERS / "crash_driver.py"), "--worker", str(case / "data"), "--action", action]
    return _run(case, command, (0, 1))


@pytest.fixture
def case(tmp_path):
    folder = tmp_path / "case"
    folder.mkdir()
    return folder


def test_main_seeds_its_own_names_and_downgrades(case):
    seeded = _legacy(case, "seed")
    assert {"U.aliases", "U.preview", "J", "H", "F", "R", "O", "S.memorialCamera", "S.rosterThumbnails"} <= set(
        seeded["writers"]
    )
    if _apfs(case):
        upgraded = _current(case)
        assert (upgraded["status"], upgraded["notice"]) == ("ready", "upgraded")
    assert _legacy(case, "downgrade")["favorites"] == []


@pytest.mark.parametrize("interrupt", ["T:afterFirstWrite", "I:publishing", "D:photoBackedUp"])
def test_interrupted_journal_recovers_in_both_codes(case, interrupt):
    receipt = _legacy(case, "seed", "--interrupt", interrupt, codes=(91,))
    assert receipt["legacyBoundary"] == interrupt
    if _apfs(case):
        assert _current(case)["status"] == "ready"
        recovered = _current(case, "recover")
        assert (recovered["recovery"], recovered["pending"]) == ("settled", [])
    assert _legacy(case, "recover")["recovery"] == "settled"


def test_interrupted_catalog_journal_recovers_or_waits(case):
    _legacy(case, "seed")
    if not _apfs(case):
        pytest.skip("Generation-2 data needs a local APFS volume")
    assert _legacy(case, "upgrade")["status"] == "ready"
    receipt = _legacy(case, "catalog", codes=(91,))
    assert "/catalog-transactions/" in receipt["catalogJournal"]
    seed = case.with_name("catalog-seed")
    shutil.copytree(case, seed, copy_function=shutil.copy2)
    recovered = _current(case, "recover")
    assert (recovered["recovery"], recovered["pending"]) == ("settled", [])
    assert [record["live"] for record in recovered["catalog"].values()] == ["official"]
    shutil.rmtree(case)
    seed.rename(case)
    preferences = case / "data" / "webui.json"
    document = json.loads(preferences.read_bytes())
    document["prefs"]["upgrade_matrix_edit"] = True
    preferences.write_text(json.dumps(document), encoding="utf-8")
    assert _current(case, "import")["reason"] == "recoveryPending"


def test_feature_detection_tolerates_a_missing_parent_package(monkeypatch):
    # Older releases lack whole packages the current code has, such as the host package.
    monkeypatch.syspath_prepend(str(DRIVERS))
    spec = importlib.util.spec_from_file_location("upgrade_matrix_legacy_driver", DRIVERS / "legacy_driver.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    assert driver._available("bmpt", "upgrade_matrix_absent.store_formats") is None
    assert driver._available("bmpt", "upgrade_matrix_absent") is None
    assert driver._available("bmpt", "host.store_formats") is not None
