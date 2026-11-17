"""The job runner: writer jobs apart from read-only ones, and what shutdown waits for."""

from __future__ import annotations

import threading
import time

import pytest

from ba_modding_toolkit.i18n import I18n
from bmpt.webui import jobs as jobs_module
from bmpt.webui.events import EventBus
from bmpt.webui.jobs import JobRunner


@pytest.mark.parametrize("language", ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR"])
def test_progress_keeps_action_labels_and_names_an_empty_callback(monkeypatch, language):
    translate = I18n(language).t
    monkeypatch.setattr(jobs_module, "t", translate)
    events = EventBus()
    seen = events.subscribe()
    jobs = JobRunner(events)
    named = translate("webui.progress.apply", name="portrait.bundle")
    try:

        def run(log, progress):
            progress(1, 1)
            progress(2, 2, named)

        jid = jobs.submit("apply", run, writes="game")
        assert jobs.wait(jid, 5)
        reports = []
        while not seen.empty():
            event, data = seen.get_nowait()
            if event == "job.progress":
                reports.append(data)
        assert [report["label"] for report in reports] == [
            translate("webui.progress.process", name=translate("webui.progress.mods")),
            named,
        ]
        assert jobs.get(jid).to_json()["progress"] == reports[-1]
        assert reports[-1] == {"id": jid, "current": 2, "total": 2, "label": named, "sequence": 2}
    finally:
        jobs.shutdown()


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_only_writer_jobs_make_the_runner_busy():
    jobs = JobRunner(EventBus())
    gate = threading.Event()
    reading = jobs.submit("extract", lambda log, progress: gate.wait(5))
    assert wait_for(lambda: jobs.get(reading).state == "running")
    assert not jobs.busy() and jobs.active("extract")
    for kind, writes in (("library.prune", "library"), ("update", "library"), ("library.clearBuilt", "library")):
        jobs.submit(kind, lambda log, progress: None, writes=writes)
    assert jobs.busy() and jobs.busy("library") and not jobs.busy("game")
    jobs.submit("mods.toggle", lambda log, progress: None, writes="game")
    assert jobs.busy("game") and jobs.active("mods.toggle")
    gate.set()
    assert wait_for(lambda: not jobs.busy())
    jobs.shutdown()


def test_shutdown_drops_queued_read_only_jobs_and_never_joins_a_running_one():
    jobs = JobRunner(EventBus())
    gate, ran = threading.Event(), []
    running = jobs.submit("extract", lambda log, progress: gate.wait(10))
    assert wait_for(lambda: jobs.get(running).state == "running")
    queued = jobs.submit("update.download", lambda log, progress: ran.append("download"))
    start = time.monotonic()
    jobs.shutdown(wait=5)
    assert time.monotonic() - start < 1  # no writer job: nothing to wait for
    gate.set()
    assert wait_for(lambda: jobs.get(queued).state == "dropped")
    assert ran == []


def test_shutdown_waits_for_a_writer_job():
    jobs = JobRunner(EventBus())
    gate, ran = threading.Event(), []
    writer = jobs.submit("mods.toggle", lambda log, progress: gate.wait(5) and ran.append("toggle"), writes="game")
    assert wait_for(lambda: jobs.get(writer).state == "running")
    threading.Timer(0.2, gate.set).start()
    jobs.shutdown(wait=5)
    assert ran == ["toggle"] and jobs.get(writer).state == "done"


def test_a_pending_job_is_never_forgotten():
    jobs = JobRunner(EventBus(), keep=2)
    gate = threading.Event()
    first = jobs.submit("slow", lambda log, progress: gate.wait(5), writes="library")
    others = [jobs.submit("x", lambda log, progress: None) for _ in range(4)]
    assert jobs.get(first).state in ("queued", "running") and jobs.busy()
    gate.set()
    assert jobs.wait(others[-1], 5)
    jobs.shutdown()


def test_failures_in_run_result_or_listeners_always_finish_and_release_the_worker():
    from bmpt.errors import ServiceError

    events = EventBus()
    seen = events.subscribe()
    jobs = JobRunner(events)

    class BadResult:
        def to_json(self):
            raise ValueError("bad result")

    def crash(error):
        def run(log, progress):
            log("building")
            progress(1, 1)
            raise error

        return run

    def listener(job):
        raise RuntimeError("listener failed")

    jobs.listen(listener)
    try:
        for fn, code in (
            (crash(ServiceError("mod_apply_failed")), "mod_apply_failed"),
            (crash(RuntimeError("build failed")), "internal"),
            (crash(SystemExit("builder exited")), "internal"),
            (lambda log, progress: BadResult(), "internal"),
        ):
            jid = jobs.submit("apply", fn, writes="game")
            assert jobs.wait(jid, 5)
            job = jobs.get(jid)
            assert job.state == "failed" and job.error["code"] == code
            assert job.finished and not jobs.busy()
        next_id = jobs.submit("restore", lambda log, progress: "restored", writes="game")
        assert jobs.wait(next_id, 5)
        assert jobs.get(next_id).result == "restored"
        messages = []
        while not seen.empty():
            messages.append(seen.get_nowait())
        assert len([d for e, d in messages if e == "job.failed"]) == 4
    finally:
        jobs.shutdown()


def test_event_delivery_failure_cannot_kill_the_job_worker():
    class BrokenEvents(EventBus):
        def publish(self, event, data=None):
            raise RuntimeError("event delivery failed")

    jobs = JobRunner(BrokenEvents())
    try:

        def apply(log, progress):
            log("applied")
            progress(1, 2, "portrait.bundle")
            progress(2, 2, "lobby.bundle")

        jid = jobs.submit("apply", apply, writes="game")
        assert jobs.wait(jid, 5)
        assert jobs.get(jid).state == "done" and not jobs.busy()
        assert jobs.get(jid).to_json()["progress"] == {
            "id": jid,
            "current": 2,
            "total": 2,
            "label": "lobby.bundle",
            "sequence": 2,
        }
        next_id = jobs.submit("restore", lambda log, progress: "ok", writes="game")
        assert jobs.wait(next_id, 5)
        assert jobs.get(next_id).result == "ok"
    finally:
        jobs.shutdown()


def test_parallel_progress_has_one_sequence_shared_with_polling():
    from concurrent.futures import ThreadPoolExecutor

    events = EventBus()
    seen = events.subscribe()
    jobs = JobRunner(events)
    start = threading.Barrier(4)

    def run(log, progress):
        def report(worker):
            start.wait(timeout=5)
            for step in range(5):
                progress({"worker": worker, "done": step, "total": 5, "sequence": -1})

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(report, range(4)))

    try:
        jid = jobs.submit("import.run", run)
        assert jobs.wait(jid, 5)
        reports = []
        while not seen.empty():
            event, data = seen.get_nowait()
            if event == "job.progress":
                reports.append(data)
        assert [report["sequence"] for report in reports] == list(range(1, 21))
        assert jobs.get(jid).to_json()["progress"] == reports[-1]
        assert len({id(report) for report in reports}) == 20
    finally:
        jobs.shutdown()


def test_user_jobs_go_before_queued_maintenance_and_a_running_job_is_not_interrupted():
    jobs = JobRunner(EventBus())
    gate, ran = threading.Event(), []
    try:
        first = jobs.submit(
            "library.prune",
            lambda log, progress: (gate.wait(5), ran.append("prune running")),
            writes="library",
            rank=jobs_module.MAINTENANCE,
        )
        assert wait_for(lambda: jobs.get(first).state == "running")
        jobs.submit(
            "mods.crcRepair",
            lambda log, progress: ran.append("crc repair"),
            writes="game",
            rank=jobs_module.MAINTENANCE,
        )
        jobs.submit(
            "mods.refreshCostumes",
            lambda log, progress: ran.append("start rebuild"),
            writes="game",
            rank=jobs_module.START,
        )
        jobs.submit("mods.toggle", lambda log, progress: ran.append("toggle"), writes="game")
        last = jobs.submit("apply", lambda log, progress: ran.append("apply"), writes="game")
        gate.set()
        assert jobs.wait(last, 5) and wait_for(lambda: len(ran) == 5)
        assert ran == ["prune running", "toggle", "apply", "start rebuild", "crc repair"]
    finally:
        gate.set()
        jobs.shutdown()


def test_queued_writers_still_run_at_shutdown_whatever_their_class():
    jobs = JobRunner(EventBus())
    gate, ran = threading.Event(), []
    blocker = jobs.submit("extract", lambda log, progress: gate.wait(5))
    assert wait_for(lambda: jobs.get(blocker).state == "running")
    jobs.submit(
        "library.prune", lambda log, progress: ran.append("prune"), writes="library", rank=jobs_module.MAINTENANCE
    )
    jobs.shutdown()
    gate.set()
    assert wait_for(lambda: ran == ["prune"])
    with pytest.raises(ValueError):
        jobs.submit("x", lambda log, progress: None, rank=7)


def test_a_cancelled_job_never_runs_when_queued_and_stops_at_its_next_step_when_running():
    """A page cancels a check it no longer wants: a queued one never runs and ends "cancelled" at once; a running
    one is told and ends "cancelled" at its next step; a job not made cancellable, or of another kind, is refused."""
    from bmpt.errors import ServiceError
    from bmpt.webui.jobs import Cancelled

    events = EventBus()
    seen = events.subscribe()
    jobs = JobRunner(events)
    gate, steps, ran = threading.Event(), [], []
    try:

        def check(log, progress, cancelled):
            steps.append("started")
            gate.wait(5)
            if cancelled():
                raise Cancelled()
            steps.append("finished")
            return {"saved": True}

        running = jobs.submit("costume-check", check, cancellable=True)
        assert wait_for(lambda: jobs.get(running).state == "running")
        queued = jobs.submit("costume-check", lambda log, progress, cancelled: ran.append("queued"), cancellable=True)
        plain = jobs.submit("extract", lambda log, progress: ran.append("extract"))
        with pytest.raises(ServiceError, match="bad_params"):
            jobs.cancel(plain, ("costume-check",))  # not cancellable
        with pytest.raises(ServiceError, match="bad_params"):
            jobs.cancel(queued, ("compatibility",))  # not a kind the caller may stop
        with pytest.raises(ServiceError, match="job_not_found"):
            jobs.cancel("j999", ("costume-check",))
        assert jobs.cancel(queued, ("costume-check",)) == "cancelled"
        assert jobs.wait(queued, 0) and jobs.get(queued).state == "cancelled" and not jobs.get(queued).pending
        assert jobs.cancel(running, ("costume-check",)) == "running"
        gate.set()
        assert jobs.wait(running, 5) and jobs.wait(plain, 5)
        assert jobs.get(running).state == "cancelled" and jobs.get(running).result is None
        assert steps == ["started"] and ran == ["extract"]  # the queued check never ran; the next job did
        assert jobs.get(running).to_json()["state"] == "cancelled"
        names = []
        while not seen.empty():
            event, data = seen.get_nowait()
            if event == "job.cancelled":
                names.append(data["id"])
        assert names == [queued, running]
        # a job that ended is left as it is
        done = jobs.submit("costume-check", lambda log, progress, cancelled: "ok", cancellable=True)
        assert jobs.wait(done, 5) and jobs.cancel(done, ("costume-check",)) == "done"
        assert jobs.get(done).result == "ok"
        with pytest.raises(ValueError):
            jobs.submit("apply", lambda log, progress, cancelled: None, writes="game", cancellable=True)
    finally:
        gate.set()
        jobs.shutdown()
