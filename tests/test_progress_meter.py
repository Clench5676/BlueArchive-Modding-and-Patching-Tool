"""The percentage of a job's item (progress_meter) and the costume build that feeds it."""

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from bmpt import progress_meter
from bmpt.progress_meter import CAP, QUIET, Meter

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
official = pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_phases_weigh_their_share_and_parts_of_a_phase_move_within_it():
    seen = []
    meter = Meter(seen.append, [("a", 1), ("b", 3), ("c", 0)], every=0)
    meter.phase("a")
    assert seen == [] and meter.fraction == 0  # nothing done yet: nothing to show
    meter.phase("b")
    assert seen == [pytest.approx(0.25)]
    meter.phase("b", 2, 3)
    assert seen[-1] == pytest.approx(0.75)
    meter.phase("unknown")  # a phase the table does not name changes nothing
    meter.phase("c")
    assert seen[-1] == pytest.approx(CAP)  # the end of the table, but the item has not ended: never 100 %
    assert all(f < 1 for f in seen)


def test_the_fraction_only_grows():
    seen = []
    meter = Meter(seen.append, {"a": 1, "b": 1}, every=0)
    meter.phase("b", 1, 2)
    meter.phase("a", 1, 2)  # an earlier phase marked later: stays where it was
    meter.phase("b", 0, 2)
    assert seen == [pytest.approx(0.75)] and meter.fraction == pytest.approx(0.75)


def test_reports_are_throttled_and_only_on_a_new_whole_percent():
    clock, seen = Clock(), []
    meter = Meter(seen.append, {"a": 1}, every=0.2, clock=clock)
    meter.phase("a", 10, 100)
    assert seen == [pytest.approx(0.10)]
    clock.now = 0.1
    meter.phase("a", 20, 100)  # within 0.2 s of the last report: held back
    assert len(seen) == 1 and meter.fraction == pytest.approx(0.20)
    clock.now = 0.3
    meter.phase("a", 2005, 10000)  # 20.05 %: the same whole percent as the fraction held back, but not as shown
    assert seen[-1] == pytest.approx(0.2005)
    clock.now = 0.6
    meter.phase("a", 2009, 10000)  # still 20 %: nothing new to show
    assert len(seen) == 2
    clock.now = 0.9
    meter.phase("a", 21, 100)
    assert seen[-1] == pytest.approx(0.21) and len(seen) == 3


def test_the_default_throttle_is_two_hundred_ms_and_a_quiet_meter_marks_nothing():
    assert progress_meter.EVERY == 0.2
    QUIET.phase("anything", 1, 2)
    assert QUIET.fraction == 0.0
    Meter(None, {"a": 1}).phase("a", 1, 2)  # no report: marking still works


def test_the_costume_table_lists_each_phase_once_with_the_physics_phases():
    from bmpt.gameassets import model_adapt

    names = [name for name, _, _ in model_adapt.COSTUME_PHASES]
    assert len(names) == len(set(names))
    assert names[0] == "stage" and names[-1] == "save"
    plain, physics = dict(model_adapt.costume_weights(False)), dict(model_adapt.costume_weights(True))
    assert plain["springs"] == 0 and physics["springs"] > 0
    assert plain["arms"] == 0 and physics["arms"] > 0  # the arms' retarget runs with Optimize Physics only
    assert physics["hems"] > plain["hems"]  # Optimize Physics judges many more rules


def test_the_library_reports_a_costume_builds_progress_through_the_job(monkeypatch, tmp_path):
    """``_stage_costume`` hands the build a meter whose growing fractions, below 1, reach its ``fraction``; a job's
    progress gets them as its item's {"current", "total", "step", "fraction"} (``_item_fraction``), the phases as
    the steps of the progress line (``mod_progress.COSTUME_STEPS``)."""
    from bmpt.gameassets import model_adapt
    from bmpt.library import service

    monkeypatch.setattr(progress_meter, "EVERY", 0)
    phases = [name for name, _, _ in model_adapt.COSTUME_PHASES if name not in ("stage", "save")]

    def adapt(**kwargs):
        for name in phases:
            kwargs["meter"].phase(name)
            kwargs["step"]()
        return SimpleNamespace(changed={})

    library = object.__new__(service.ModLibrary)
    library.root = tmp_path
    monkeypatch.setattr(model_adapt, "adapt", adapt)
    monkeypatch.setattr(service.ModLibrary, "_official_or_current", lambda self, path, pin: path)
    monkeypatch.setattr(service.ModLibrary, "_stage_copies", lambda self, originals, folder: {})
    monkeypatch.setattr(service.ModLibrary, "_costume_files", lambda self, other, pin: ("dev", [], []))
    monkeypatch.setattr(service.ModLibrary, "_cab_index", lambda self, pin: None)
    monkeypatch.setattr(service.ModLibrary, "_core_map", lambda self, pin: type("M", (), {"slot": lambda s, c: c})())
    target = tmp_path / "character-hina_original-_mxload-2026-01-01_assets_all_1.bundle"
    reported, steps = [], []
    library._stage_costume("other", [target], tmp_path, print, None, step=lambda: steps.append(1),
                           fraction=service._item_fraction(reported.append, 2, 5), physics=True)
    assert len(steps) == len(phases)
    assert reported and all(set(p) == {"current", "total", "step", "fraction"} and (p["current"], p["total"]) == (2, 5)
                            for p in reported)
    fractions = [p["fraction"] for p in reported]
    assert fractions == sorted(fractions) and fractions[-1] < 0.9  # the rest of the item for the write after it
    steps = [p["step"] for p in reported]
    assert list(dict.fromkeys(steps)) == ["import.step.readModel", "import.step.fitCostume", "import.step.buildModel",
                                              "import.step.save"]
    # without a job's progress the build marks its phases all the same, and nothing is reported
    assert service._item_fraction(None) is None
    library._stage_costume("other", [target], tmp_path, print, None)
    single = []
    service._item_fraction(single.append, build=1.0)(0.12345)
    assert single == [{"current": 1, "total": 1, "step": "", "fraction": 0.123}]


@official
@pytest.mark.parametrize("target,source,physics", [("hina_original", "ch0288", False), ("hina_original", "ch0288", True)])
def test_official_a_costume_build_passes_its_phases_in_order_with_growing_fractions(target, source, physics):
    from ba_modding_toolkit.bundle import Bundle
    from bmpt.gameassets import model_adapt as ma
    from bmpt.gameassets.discovery import cab_names

    files = list(GAME_DIR.rglob("*.bundle"))
    index = {}
    for path in files:
        if re.search(r"characters|-_mx-npcs-|^character-|-_mx-shaders-|-_mx-program-animators-|^field-|animatorbase|"
                     r"prolog|-_mx-effectsfreezed-", path.name):
            for cab in cab_names(path):
                index[cab] = path
    names = [name for name, _, _ in ma.COSTUME_PHASES]

    class Recorder(Meter):
        def __init__(self):
            super().__init__(fractions.append, ma.costume_weights(physics), every=0)
            self.order = []

        def phase(self, name, done=0, count=1):
            if not self.order or self.order[-1] != name:
                self.order.append(name)
            super().phase(name, done, count)

    fractions = []
    meter = Recorder()
    slot = [p for p in files if re.match(rf"(character|assets-_mx-characters)-{re.escape(target)}-_mx", p.name)]
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    ma.adapt(index=index, resolver=None, target_files=slot, target_dev=target, source_files=sources,
             source_dev=source, open_writable=Bundle.load, physics=physics, halo=True, meter=meter)
    # every phase marked is in the table, in the table's order
    assert meter.order and set(meter.order) <= set(names)
    assert [names.index(name) for name in meter.order] == sorted(names.index(name) for name in meter.order)
    assert {"read", "plan", "hems", "curves", "check"} <= set(meter.order)
    assert ("arms" in meter.order) == physics  # Hina <- Aoba: the arms' retarget marks each clip it comes to
    assert fractions == sorted(set(fractions)) and fractions[-1] < 1
    # the build alone (no stage, no save) ends where the table's save begins
    start_of_save = 1 - dict(ma.costume_weights(physics))["save"] / sum(w for _, w in ma.costume_weights(physics))
    assert meter.fraction <= start_of_save + 1e-9
