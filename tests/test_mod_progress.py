"""The progress line of a job over mods (library/mod_progress.py): the item at hand, who it is, its step and share."""

from bmpt.core import progress_meter
from bmpt.library import mod_progress
from bmpt.library.mod_progress import COSTUME_STEPS, ItemProgress, STEP_CHECK, STEP_RESTORE, STEP_WRITE
from bmpt.core.progress_meter import Meter


def test_each_item_is_reported_as_it_starts_counted_from_one_with_who_it_is():
    seen = []
    line = ItemProgress(seen.append, 13, "process")
    line.item(1, alt="ch0063", kind="model", name="My Swim Edit", step=STEP_CHECK)
    assert seen == [
        {
            "current": 1,
            "total": 13,
            "action": "process",
            "alt": "ch0063",
            "kind": "model",
            "name": "My Swim Edit",
            "source": "",
            "core": "",
            "step": STEP_CHECK,
            "fraction": 0.0,
        }
    ]
    line.step(STEP_CHECK)  # already shown: nothing
    line.fraction(0.0)
    assert len(seen) == 1
    line.item(2, alt="ch0145", kind="model", source="ch0063", action="apply")
    assert (seen[-1]["current"], seen[-1]["source"], seen[-1]["action"], seen[-1]["step"]) == (2, "ch0063", "apply", "")


def test_the_share_only_grows_within_an_item_and_starts_again_with_the_next():
    seen = []
    line = ItemProgress(seen.append, 2)
    line.item(1, alt="a", kind="spr")
    line.fraction(0.4)
    line.fraction(0.3)
    line.step("import.step.save", at=0.2)  # a step that starts earlier keeps the share shown
    assert [(p["step"], p["fraction"]) for p in seen] == [("", 0.0), ("", 0.4), ("import.step.save", 0.4)]
    line.item(2, alt="b", kind="spr")
    assert seen[-1]["fraction"] == 0.0


def test_a_build_fills_its_share_of_the_item_and_the_write_starts_where_it_ended():
    seen = []
    line = ItemProgress(seen.append, 1)
    line.item(1, alt="a", kind="model")
    part = line.part(0.0, 1.0, build=0.9)
    part.phase("read")
    part(0.5)
    part.phase("save")
    part(1.0)
    part.phase("write")
    assert [(p["step"], p["fraction"]) for p in seen[1:]] == [
        (COSTUME_STEPS["read"], 0.0),
        (COSTUME_STEPS["read"], 0.45),
        (COSTUME_STEPS["save"], 0.45),
        (COSTUME_STEPS["save"], 0.9),
        (STEP_WRITE, 0.9),
    ]
    # nothing built: the write starts halfway through the share; the official files put back are their own step
    line.item(1, alt="a", kind="spr")
    plain = line.part(0.0, 1.0, build=0.9)
    plain.phase("restore")
    assert (seen[-1]["step"], seen[-1]["fraction"]) == (STEP_RESTORE, 0.5)
    line.done()
    assert seen[-1]["fraction"] == 1.0
    # a later share of the item: its write at its start when it builds nothing
    line.item(1, alt="a", kind="model")
    line.part(0.0, 0.9)(1.0)
    line.part(0.9, 1.0, build=0.0).phase("write")
    assert (seen[-1]["step"], seen[-1]["fraction"]) == (STEP_WRITE, 0.9)


def test_without_a_job_nothing_is_reported_and_a_plain_callback_is_told_no_phase():
    line = ItemProgress(None, 3)
    line.item(1, alt="a", kind="spr")
    line.part(0.0, 1.0)(0.5)
    assert mod_progress.single(None) is None
    plain = []
    mod_progress.entered(plain.append, "write")
    assert plain == []


def test_a_single_items_build_reports_its_steps_and_share_only():
    seen = []
    part = mod_progress.single(seen.append, 1, 1, build=0.9)
    part.phase("plan")
    part(0.5)
    mod_progress.entered(part, "write")
    assert seen == [
        {"current": 1, "total": 1, "step": COSTUME_STEPS["plan"], "fraction": 0.0},
        {"current": 1, "total": 1, "step": COSTUME_STEPS["plan"], "fraction": 0.45},
        {"current": 1, "total": 1, "step": STEP_WRITE, "fraction": 0.9},
    ]


def test_every_costume_phase_has_a_step_and_the_meter_tells_each_phase_it_enters(monkeypatch):
    from bmpt.gameassets import model_adapt

    assert {name for name, _, _ in model_adapt.COSTUME_PHASES} == set(COSTUME_STEPS)
    monkeypatch.setattr(progress_meter, "EVERY", 0)
    entered = []

    class Report:
        def __call__(self, fraction):
            pass

        def phase(self, name):
            entered.append(name)

    meter = Meter(Report(), [("a", 1), ("b", 1)])
    meter.phase("a")
    meter.phase("a", 1, 2)  # within the same phase: told once
    meter.phase("b")
    meter.phase("missing")  # not one of the build's phases: nothing
    assert entered == ["a", "b"]


def test_a_toggles_dict_progress_counts_the_items_before_the_one_at_hand(monkeypatch):
    from bmpt.webui.services import Services

    published, forwarded = [], []
    services = object.__new__(Services)

    class Snapshot:
        value = None

        def progress(self):
            return self.value

        def set_progress(self, value):
            self.value = value

    services.snapshot = Snapshot()
    services._progress_at = 0.0
    services.publish_state = lambda: published.append(services.snapshot.value)
    report = services._progress(lambda *args: forwarded.append(args))
    report({"current": 3, "total": 5, "step": STEP_CHECK})
    report({"current": 3, "total": 5, "step": STEP_WRITE})
    assert services.snapshot.value == {"done": 2, "total": 5}
    assert len(forwarded) == 2 and published == [{"done": 2, "total": 5}]
    report({"current": 3, "total": 5, "step": STEP_WRITE, "fraction": 1.0})  # the item ended
    assert services.snapshot.value == {"done": 3, "total": 5}
    report(5, 5, "")
    assert services.snapshot.value == {"done": 5, "total": 5}
