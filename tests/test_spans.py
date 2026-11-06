"""Timing spans below the web UI reach the session's timings (BL-092)."""

from __future__ import annotations

import threading
import time

from bmpt import spans
from bmpt.webui.timing import Timings


def test_spans_record_waits_holds_and_the_longest_value():
    timings = Timings()
    spans.install(timings)
    try:
        lock = threading.Lock()
        with spans.held("model-lock.model", lock, hold=True):
            pass
        assert "model-lock.model.wait" not in timings.snapshot()  # an uncontended lock records no wait
        assert timings.snapshot()["model-lock.model.held"]["count"] == 1
        lock.acquire()
        threading.Timer(0.05, lock.release).start()
        with spans.held("model-lock.model", lock):
            pass
        wait = timings.snapshot()["model-lock.model.wait"]
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
