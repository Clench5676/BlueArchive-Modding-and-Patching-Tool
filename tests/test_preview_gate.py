"""Foreground model exports let scans pause without starving or delaying cancellation."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bmpt import preview_gate
from bmpt.library.service import _View
from bmpt.webui.services import Services


@pytest.fixture
def records(monkeypatch):
    recorded = []
    monkeypatch.setattr(preview_gate.spans, "record", lambda name, seconds: recorded.append((name, seconds)))
    return recorded


@pytest.fixture
def gate_wait(monkeypatch):
    waiting = threading.Event()
    timeouts = []
    wait = preview_gate._condition.wait

    def observed(timeout):
        timeouts.append(timeout)
        waiting.set()
        return wait(timeout)

    monkeypatch.setattr(preview_gate._condition, "wait", observed)
    return waiting, timeouts


def test_idle_gate_returns_without_waiting_or_recording(gate_wait, records):
    preview_gate.yield_to_previews(None)
    assert not gate_wait[0].is_set()
    assert records == []


def test_foreground_counts_nested_exports_and_releases_after_exception(gate_wait, records):
    with ThreadPoolExecutor(1) as pool:
        with preview_gate.foreground():
            with pytest.raises(ValueError, match="export failed"):
                with preview_gate.foreground():
                    assert preview_gate._count == 2
                    raise ValueError("export failed")
            assert preview_gate._count == 1
            future = pool.submit(preview_gate.yield_to_previews, None)
            assert gate_wait[0].wait(2)
            assert not future.done()
        future.result(timeout=2)
    assert preview_gate._count == 0
    assert len(records) == 1 and records[0][0] == "scan.yield" and records[0][1] > 0


def test_last_export_notifies_all_waiters(monkeypatch):
    ready = threading.Event()
    waiting = 0
    wait = preview_gate._condition.wait

    def until_notified(timeout):
        nonlocal waiting
        waiting += 1
        if waiting == 2:
            ready.set()
        # A long timeout proves exit wakes every waiter without relying on polling.
        return wait(5)

    monkeypatch.setattr(preview_gate._condition, "wait", until_notified)
    with ThreadPoolExecutor(2) as pool:
        with preview_gate.foreground():
            futures = [pool.submit(preview_gate.yield_to_previews, None) for _ in range(2)]
            assert ready.wait(timeout=2)
        for future in futures:
            future.result(timeout=2)


@pytest.mark.parametrize("cancel, limit", [(lambda: True, 20.0), (None, 0.0)])
def test_cancelled_or_expired_gate_does_not_wait_or_record(gate_wait, records, cancel, limit):
    with preview_gate.foreground():
        preview_gate.yield_to_previews(cancel, limit)
    assert not gate_wait[0].is_set()
    assert records == []


def test_cancel_during_pause_returns_while_export_is_still_running(gate_wait, records):
    cancel = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        with preview_gate.foreground():
            future = pool.submit(preview_gate.yield_to_previews, cancel.is_set)
            assert gate_wait[0].wait(2)
            cancel.set()
            future.result(timeout=2)
            assert preview_gate._count == 1
    assert all(timeout <= 0.1 for timeout in gate_wait[1])
    assert len(records) == 1 and records[0][0] == "scan.yield"


def test_pause_limit_allows_scan_to_continue_with_export_running(gate_wait, records):
    with ThreadPoolExecutor(1) as pool:
        with preview_gate.foreground():
            future = pool.submit(preview_gate.yield_to_previews, None, 0.03)
            future.result(timeout=2)
            assert preview_gate._count == 1
    assert gate_wait[1] and all(timeout <= 0.03 for timeout in gate_wait[1])
    assert len(records) == 1 and records[0][0] == "scan.yield" and records[0][1] >= 0.02


def fake_view():
    view = _View.__new__(_View)
    view.cores = lambda pin: {"a", "b"}
    read = []

    def entry(pin, core):
        read.append(core)
        return SimpleNamespace(core=core, configured=True)

    view.entry = entry
    return view, read


def test_scan_entries_pause_then_resume_and_yield_once_per_core(gate_wait, monkeypatch):
    view, read = fake_view()
    yielded = []
    yield_to_previews = preview_gate.yield_to_previews

    def yielding(cancel):
        yielded.append(cancel)
        yield_to_previews(cancel)

    monkeypatch.setattr(preview_gate, "yield_to_previews", yielding)
    with ThreadPoolExecutor(1) as pool:
        with preview_gate.foreground():
            future = pool.submit(view.entries, [SimpleNamespace(key="install")], background=True)
            assert gate_wait[0].wait(2)
            assert read == [] and not future.done()
        assert [entry.core for entry in future.result(timeout=2)] == ["a", "b"]
    assert read == ["a", "b"] and yielded == [None, None]


def test_scan_cancelled_during_pause_stops_before_reading(gate_wait):
    view, read = fake_view()
    cancel = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        with preview_gate.foreground():
            future = pool.submit(view.entries, [SimpleNamespace(key="install")], cancel.is_set, background=True)
            assert gate_wait[0].wait(2)
            cancel.set()
            assert future.result(timeout=2) == []
            assert read == []


def test_entries_under_the_library_lock_never_pause(monkeypatch):
    # the Mods-off plan reads entries while holding the library lock, which a preview may need
    view, read = fake_view()
    monkeypatch.setattr(preview_gate, "yield_to_previews", lambda cancel: pytest.fail("paused"))
    with preview_gate.foreground():
        assert [entry.core for entry in view.entries([SimpleNamespace(key="install")])] == ["a", "b"]
    assert read == ["a", "b"]


@pytest.mark.parametrize("source", ["live", "official", "mod:1", "import:1", "variant:other"])
@pytest.mark.parametrize("fails", [False, True])
def test_model_asset_gate_covers_each_source_and_releases_on_failure(source, fails):
    def export(core, target, selected, path):
        assert preview_gate._count == 1
        assert (core, target, selected) == ("hero", "hero", source)
        if fails:
            raise ValueError("export failed")
        return b"glTF"

    services = SimpleNamespace(safe_mode=False,
                               catalog=SimpleNamespace(variant=lambda key: SimpleNamespace(model="hero", key=key)),
                               _model_asset=export)
    if fails:
        with pytest.raises(ValueError, match="export failed"):
            Services.asset(services, "model/hero.glb", {"source": source})
    else:
        Services.asset(services, "model/hero.glb", {"source": source})
    assert preview_gate._count == 0
