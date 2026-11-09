"""Concurrent requests share model export bytes and failures, even without cache retention."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from bmpt import spans
from bmpt.gameassets import AssetCache, GameAssets, model


@pytest.fixture
def exporting(tmp_path, monkeypatch):
    root = tmp_path / "game"
    root.mkdir()
    prefab = root / "character-ch0242-_mxload-2024-11-18_assets_all_1.bundle"
    prefab.write_bytes(b"prefab")
    game = GameAssets([root], AssetCache(root=tmp_path / "cache", max_bytes=0))
    entered, release, joined = threading.Event(), threading.Event(), threading.Event()
    records = []
    span = spans.span

    @contextmanager
    def observed_span(name):
        with span(name):
            if name == "export.model.join":
                joined.set()
            yield

    monkeypatch.setattr(spans, "span", observed_span)
    monkeypatch.setattr(spans, "record", lambda name, seconds: records.append((name, seconds)))
    yield game, prefab, entered, release, joined, records
    release.set()


def test_two_requests_share_one_export_without_cached_bytes(exporting, monkeypatch):
    game, prefab, entered, release, joined, records = exporting
    builds = []
    data = b"glTF shared bytes"

    def export(graph, bundles, core):
        builds.append(core)
        graph.loaded.append(str(prefab))
        entered.set()
        assert release.wait(5)
        return data

    monkeypatch.setattr(model, "export_chibi", export)
    with ThreadPoolExecutor(2) as pool:
        try:
            leader = pool.submit(game.model_glb, "ch0242")
            assert entered.wait(2)
            follower = pool.submit(game.model_glb, "ch0242")
            assert joined.wait(2)
            assert not follower.done()
        finally:
            release.set()
        assert leader.result(timeout=2) is data
        assert follower.result(timeout=2) is data
    assert builds == ["ch0242"]
    assert game._model_flights == {}
    waits = [seconds for name, seconds in records if name == "export.model.join"]
    assert len(waits) == 1 and waits[0] > 0
    assert game.model_glb("ch0242") is data
    assert builds == ["ch0242", "ch0242"], "the finished flight must be removed even on success"


def test_failure_is_shared_and_next_request_exports_again(exporting, monkeypatch):
    game, prefab, entered, release, joined, records = exporting
    builds = []
    failure = ValueError("model export failed")

    def export(graph, bundles, core):
        builds.append(core)
        graph.loaded.append(str(prefab))
        if len(builds) == 1:
            entered.set()
            assert release.wait(5)
            raise failure
        return b"glTF retry"

    monkeypatch.setattr(model, "export_chibi", export)
    with ThreadPoolExecutor(2) as pool:
        try:
            leader = pool.submit(game.model_glb, "ch0242")
            assert entered.wait(2)
            follower = pool.submit(game.model_glb, "ch0242")
            assert joined.wait(2)
        finally:
            release.set()
        for future in (leader, follower):
            with pytest.raises(ValueError, match="model export failed") as caught:
                future.result(timeout=2)
            assert caught.value is failure
    assert builds == ["ch0242"] and game._model_flights == {}
    assert len([name for name, _ in records if name == "export.model.join"]) == 1
    assert game.model_glb("ch0242") == b"glTF retry"
    assert builds == ["ch0242", "ch0242"] and game._model_flights == {}
