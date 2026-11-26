"""The count a start-up job shows in Tasks over its phases and its runs after a handover."""

from bmpt.webui.services_jobs import _Count


def _shown(reports):
    return [(r["current"], r["total"]) for r in reports]


def test_two_phases_count_on_under_one_total():
    reports = []
    count = _Count()
    first = count.phase(lambda d, total=None, label="": reports.append(d), later=3)
    for i in (1, 2):
        first({"current": i, "total": 2, "step": "x"})
    second = count.phase(lambda d, total=None, label="": reports.append(d))
    for i in (1, 2, 3):
        second({"current": i, "total": 3})
    assert _shown(reports) == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    assert reports[0]["step"] == "x"


def test_a_second_phase_smaller_than_estimated_ends_the_count_there():
    reports = []
    count = _Count()
    first = count.phase(lambda d, total=None, label="": reports.append(d), later=4)
    first({"current": 1, "total": 1})
    second = count.phase(lambda d, total=None, label="": reports.append(d))
    second({"current": 1, "total": 2})
    second({"current": 2, "total": 2})
    assert _shown(reports) == [(1, 5), (2, 3), (3, 3)]
    assert [c for c, _ in _shown(reports)] == sorted(c for c, _ in _shown(reports))


def test_a_run_after_a_handover_counts_on_from_the_items_done():
    reports = []
    count = _Count()
    run = count.phase(lambda d, total=None, label="": reports.append(d))
    for i in (1, 2, 3):
        run({"current": i, "total": 10})
    # handed over after item 3; the next run lists the 7 left and counts them from 1
    run = count.phase(lambda d, total=None, label="": reports.append(d))
    run({"current": 1, "total": 7})
    run({"current": 7, "total": 7})
    assert _shown(reports)[-2:] == [(4, 10), (10, 10)]


def test_plain_reports_and_reports_without_an_item_pass_on():
    calls = []
    count = _Count()
    report = count.phase(lambda *a: calls.append(a), later=2)
    report(1, 3, "Working")
    report({"step": "publish"})
    assert calls[0] == (1, 5, "Working")
    assert calls[1][0] == {"step": "publish"}
