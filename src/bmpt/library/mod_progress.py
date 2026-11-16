"""The progress line of a job over mods, the line the web UI shows in the bottom-left corner while it runs.

A report is the job's progress dict: ``current`` (the item at hand, counted from 1) of ``total``, ``action`` (the job's
verb: process, apply, restore, check, add, export), who the item is (``variant``, ``kind``, ``core`` for a file no variant
owns; ``name``: the mod's own name, or ``source``: the variant whose costume or files it takes), its ``step`` (a web UI string key) and ``fraction`` (how far
the item is, 0..1, only growing within the item). The web UI words it (job-progress.js ``modLine``).

A long build reports its fraction through a part of the item (``ItemProgress.part``): a ``progress_meter.Meter``
report whose phases become the line's steps (``COSTUME_STEPS``).
"""

from __future__ import annotations

from typing import Any, Callable

ProgressCallback = Callable[..., None]

STEP_CHECK = "import.step.check"
STEP_ADD = "import.step.add"
STEP_EXPORT = "import.step.export"
STEP_SAVE = "import.step.save"
STEP_WRITE = "import.step.write"
STEP_RESTORE = "import.step.restore"

# A costume build's phases (model_adapt.COSTUME_PHASES) as the steps the line names: the models read, the costume
# fitted to the slot, the model built, its files saved.
COSTUME_STEPS = {
    **dict.fromkeys(("stage", "read", "skeleton", "clips", "idle", "worn", "probes"), "import.step.readModel"),
    **dict.fromkeys(("plan", "arms", "hems", "rigid", "springs", "face", "chains"), "import.step.fitCostume"),
    **dict.fromkeys(("open", "materials", "meshes", "carried", "halo", "prefabs", "curves", "check"),
                    "import.step.buildModel"),
    "save": STEP_SAVE,
}
# The phases of a write into the game files, after any build: a mod's files written, or the official ones put back.
WRITES = {"write": STEP_WRITE, "restore": STEP_RESTORE}


class Part:
    """A share of an item, ``start`` to ``end`` of its fraction, handed to the work that does it. Called with how far
    a build in it is (0..1), which fills the first ``build`` of the share; told the phase it enters (``phase``: a
    ``Meter`` report's build phases, then "write" or "restore" when the game files are written), which becomes the
    line's step. A write starts where the build ended, at the share's start when it holds no build (``build`` 0), or
    halfway through it when its build was not needed."""

    def __init__(self, owner: "ItemProgress", start: float, end: float, steps: dict[str, str] | None,
                 build: float = 1.0):
        self._owner, self._start, self._end, self._steps = owner, start, end, steps or {}
        self._build, self._built = build, False

    def __call__(self, fraction: float) -> None:
        f = min(1.0, max(0.0, float(fraction)))
        self._built = True
        self._owner.fraction(self._start + (self._end - self._start) * self._build * f)

    def phase(self, name: str) -> None:
        if name in WRITES:
            # a share with no build in it is all write; one whose build was skipped (a built copy) half judging
            share = self._build if self._built or not self._build else 0.5
            self._owner.step(WRITES[name], at=self._start + (self._end - self._start) * share)
            return
        step = self._steps.get(name)
        if step:
            self._owner.step(step)


def entered(part: Callable[[float], None] | None, name: str) -> None:
    """Tell a build's report (a ``Part``) the phase ``name`` it enters; nothing for a plain fraction callback."""
    phase = getattr(part, "phase", None)
    if phase is not None:
        phase(name)


class ItemProgress:
    """The progress of a job's items, one after another: ``item`` starts the next one (who it is, its first step),
    ``step`` and ``fraction`` move it on, ``part`` hands a share of it to a build. Each change is reported at once as
    the job's progress dict; a fraction that does not grow, or a step already shown, reports nothing."""

    def __init__(self, progress: ProgressCallback | None, total: int, action: str = ""):
        self._progress, self.total, self.action = progress, total, action
        self._item: dict[str, Any] = {}
        self._step, self._fraction = "", 0.0

    def item(self, current: int, *, variant: str = "", kind: str = "", name: str = "", source: str = "",
             core: str = "", step: str = "", action: str = "") -> None:
        self._item = {"current": current, "variant": variant, "kind": kind, "name": name, "source": source,
                      "core": core, "action": action or self.action}
        self._step, self._fraction = step, 0.0
        self._emit()

    def step(self, key: str, at: float | None = None) -> None:
        """The item is in step ``key``; ``at``: the share of the item done when it starts."""
        moved = at is not None and at > self._fraction
        if key == self._step and not moved:
            return
        self._step = key
        if moved:
            self._fraction = min(1.0, at)
        self._emit()

    def fraction(self, value: float) -> None:
        if value <= self._fraction:
            return
        self._fraction = min(1.0, value)
        self._emit()

    def done(self) -> None:
        """The item at hand has ended: its whole share, so a count of the items done includes it."""
        self.fraction(1.0)

    def part(self, start: float, end: float, steps: dict[str, str] | None = COSTUME_STEPS,
             build: float = 1.0) -> Part:
        return Part(self, start, end, steps, build)

    def _emit(self) -> None:
        if self._progress is None or not self._item:
            return
        self._progress({**self._item, "total": self.total, "step": self._step,
                        "fraction": round(self._fraction, 3)})


def single(progress: ProgressCallback | None, current: int = 1, total: int = 1, build: float = 0.9) -> Part | None:
    """How far one item's build is, for a job the web UI names itself (a student page's action): its progress dict
    carries ``current``, ``total``, ``step`` and ``fraction`` only. None without a job's progress."""
    if progress is None:
        return None

    class _Plain(ItemProgress):
        def _emit(self) -> None:
            self._progress({"current": current, "total": total, "step": self._step,
                            "fraction": round(self._fraction, 3)})

    owner = _Plain(progress, total)
    owner._item = {"current": current}
    return owner.part(0.0, 1.0, build=build)
