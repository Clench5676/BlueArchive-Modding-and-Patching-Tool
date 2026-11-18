"""Timing spans below the web UI reach the session's timings."""

from __future__ import annotations

import threading
import time

from bmpt.core import spans
from bmpt.webui.timing import Timings


def test_spans_record_waits_holds_and_the_longest_value():
    timings = Timings()
    spans.install(timings)
    try:
        lock = threading.Lock()
        with spans.held("chibi-lock.chibi", lock, hold=True):
            pass
        assert "chibi-lock.chibi.wait" not in timings.snapshot()  # an uncontended lock records no wait
        assert timings.snapshot()["chibi-lock.chibi.held"]["count"] == 1
        lock.acquire()
        threading.Timer(0.05, lock.release).start()
        with spans.held("chibi-lock.chibi", lock):
            pass
        wait = timings.snapshot()["chibi-lock.chibi.wait"]
        assert wait["count"] == 1 and wait["max"] >= 0.04
        with spans.span("asset.model.official"):
            time.sleep(0.01)
        timings.record("asset.model.official", 0.001)
        value = timings.snapshot()["asset.model.official"]
        assert value["count"] == 2 and value["max"] >= 0.01 > 0.001
    finally:
        spans.install(None)


def test_spans_without_a_sink_record_nothing():
    spans.install(None)
    with spans.span("anything"), spans.held("lock", threading.Lock(), hold=True):
        pass


def test_unfinished_spans_waits_and_measures_are_listed_until_they_end():
    timings = Timings()
    lock = threading.Lock()
    lock.acquire()
    entered, leave = threading.Event(), threading.Event()

    def waiter():
        with spans.held("chibi-lock.chibi", lock):
            entered.set()
            leave.wait(2)

    with spans.span("asset.model.mod"), timings.measure("scan.files"):
        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.02)
        listed = spans.active()
        names = [item["name"] for item in listed]
        assert names[:2] == ["asset.model.mod", "scan.files"]  # the oldest first
        assert "chibi-lock.chibi.wait" in names and "chibi-lock.chibi.held" not in names
        assert listed[0]["seconds"] >= 0.02
        lock.release()
        assert entered.wait(2)
        names = [item["name"] for item in spans.active()]
        assert "chibi-lock.chibi.held" in names and "chibi-lock.chibi.wait" not in names
        leave.set()
        thread.join(2)
    assert spans.active() == []
    assert not lock.locked()


def test_a_failing_block_releases_its_lock_and_leaves_the_list():
    lock = threading.Lock()
    try:
        with spans.held("lock", lock, hold=True), spans.span("export.chibi.convert"):
            raise ValueError
    except ValueError:
        pass
    assert not lock.locked() and spans.active() == []
