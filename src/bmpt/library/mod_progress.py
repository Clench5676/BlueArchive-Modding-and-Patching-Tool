"""The progress line of a job over mods, the line the web UI shows in the bottom-left corner while it runs.

A report is the job's progress dict: ``current`` (the item at hand, counted from 1) of ``total``, ``action`` (the job's
verb: process, apply, restore, check, add, export), who the item is (``alt``, ``kind``, ``core`` for a file
no alt owns; ``name``: the mod's own name, or ``source``: the alt whose costume or files it takes),
its ``step`` (a web UI string key) and ``fraction`` (how far the item is, 0..1, only growing within the item).
The web UI words it (job-progress.js ``modLine``).

A long build reports its fraction through a part of the item (``ItemProgress.part``): a ``progress_meter.Meter``
report whose phases become the line's steps (``COSTUME_STEPS``).
"""

from __future__ import annotations

from typing import Any, Callable

from . import toggle_timing

ProgressCallback = Callable[..., None]

STEP_CHECK = "import.step.check"
STEP_ADD = "import.step.add"
STEP_EXPORT = "import.step.export"
STEP_SAVE = "import.step.save"
STEP_WRITE = "import.step.write"
STEP_RESTORE = "import.step.restore"

# Group detailed costume-build phases into the UI's coarser progress steps.
COSTUME_STEPS = {
    **dict.fromkeys(("stage", "read", "skeleton", "clips", "idle", "worn", "probes"), "import.step.readChibi"),
    **dict.fromkeys(("plan", "arms", "hems", "rigid", "springs", "face", "chains"), "import.step.fitCostume"),
    **dict.fromkeys(
        ("open", "materials", "meshes", "carried", "halo", "prefabs", "curves", "check"), "import.step.buildChibi"
    ),
    "save": STEP_SAVE,
}
WRITES = {"write": STEP_WRITE, "restore": STEP_RESTORE}


class Part:
    """Report progress within a share of one item.

    The share runs from ``start`` to ``end`` of the item's fraction. Called with how far
    a build in it is (0..1), which fills the first ``build`` of the share; told the phase it enters (``phase``: a
    ``Meter`` report's build phases, then "write" or "restore" when the game files are written), which becomes the
    line's step. A write starts where the build ended, at the share's start when it holds no build (``build`` 0), or
    halfway through it when its build was not needed.
    """

    def __init__(
        self, owner: "ItemProgress", start: float, end: float, steps: dict[str, str] | None, build: float = 1.0
    ):
        """Allocate an item's fraction to a build and its following write.

        Args:
            owner: Item progress receiving the mapped fractions and steps.
            start: Beginning of this share within the item, from 0 to 1.
            end: End of this share within the item, from 0 to 1.
            steps: Build-phase names mapped to UI string keys; None leaves build steps unchanged.
            build: Fraction of this item's allocated interval filled by build reports before writing begins.
        """
        self._owner, self._start, self._end, self._steps = owner, start, end, steps or {}
        self._build, self._built = build, False

    def __call__(self, fraction: float) -> None:
        """Map a build's fraction, clamped to 0 through 1, into its item share."""
        f = min(1.0, max(0.0, float(fraction)))
        self._built = True
        self._owner.fraction(self._start + (self._end - self._start) * self._build * f)

    def phase(self, name: str) -> None:
        """Report a build phase or the beginning of a game-file write.

        Args:
            name: Build phase mapped by steps, or write/restore to advance past the build share.
        """
        if name in WRITES:
            # a share with no build in it is all write; one whose build was skipped (a built copy) half judging
            share = self._build if self._built or not self._build else 0.5
            self._owner.step(WRITES[name], at=self._start + (self._end - self._start) * share)
            return
        step = self._steps.get(name)
        if step:
            self._owner.step(step)


def entered(part: Callable[[float], None] | None, name: str) -> None:
    """Report a phase when the build's progress callback supports phase reporting.

    Args:
        part: A Part or fraction callback; None and plain callbacks receive no phase report.
        name: Build-phase name, or write/restore for a game-file operation.
    """
    phase = getattr(part, "phase", None)
    if phase is not None:
        phase(name)


class ItemProgress:
    """Report a job's items sequentially, with monotonic progress within each item.

    ``item`` starts the next one (who it is, its first step),
    ``step`` and ``fraction`` move it on, ``part`` hands a share of it to a build. Each change is reported at once as
    the job's progress dict; a fraction that does not grow, or a step already shown, reports nothing.
    """

    def __init__(self, progress: ProgressCallback | None, total: int, action: str = ""):
        """Start a job's progress line without selecting an item.

        Args:
            progress: Receives progress dictionaries; None disables reporting.
            total: Number of items in the job.
            action: Default UI verb for items, such as apply, restore, or check.
        """
        self._progress, self.total, self.action = progress, total, action
        self._item: dict[str, Any] = {}
        self._step, self._fraction = "", 0.0

    def item(
        self,
        current: int,
        *,
        alt: str = "",
        resource_type: str = "",
        name: str = "",
        source: str = "",
        core: str = "",
        step: str = "",
        action: str = "",
    ) -> None:
        """Start an item and reset its fraction to zero.

        Args:
            current: One-based position of the item in the job.
            alt: alt key identifying the affected student, or empty when unavailable.
            resource_type: Asset kind used to label the operation, or empty when unavailable.
            name: Mod label to show for this item, or empty when it has none.
            source: alt whose costume or files are borrowed, or empty for a mod's own files.
            core: Bundle core identifying a file without an alt, or empty when unused.
            step: Initial UI string key for the item's current phase.
            action: UI verb overriding the job's default; empty uses the default.
        """
        self._item = {
            "current": current,
            "alt": alt,
            "resource_type": resource_type,
            "name": name,
            "source": source,
            "core": core,
            "action": action or self.action,
        }
        self._step, self._fraction = step, 0.0
        self._emit()

    def step(self, key: str, at: float | None = None) -> None:
        """Advance the displayed step and optionally the item's fraction.

        Args:
            key: UI string key describing the new phase.
            at: Fraction complete at phase entry; None keeps the current fraction.
                Fractions never decrease and are capped at 1.
        """
        moved = at is not None and at > self._fraction
        if key == self._step and not moved:
            return
        self._step = key
        if moved:
            self._fraction = min(1.0, at)
        self._emit()

    def fraction(self, value: float) -> None:
        """Report a larger item fraction, capped at 1; ignore values that do not advance it."""
        if value <= self._fraction:
            return
        self._fraction = min(1.0, value)
        self._emit()

    def done(self) -> None:
        """Complete the current item's fraction so it counts among the finished items."""
        self.fraction(1.0)

    def part(self, start: float, end: float, steps: dict[str, str] | None = COSTUME_STEPS, build: float = 1.0) -> Part:
        """Allocate a share of this item to a build and its following write.

        Args:
            start: Beginning of the share within the item, from 0 to 1.
            end: End of the share within the item, from 0 to 1.
            steps: Build-phase names mapped to UI string keys; None suppresses build-step changes.
            build: Fraction of the share reserved for building; zero reserves it all for writing.
        """
        return Part(self, start, end, steps, build)

    def _emit(self) -> None:
        if self._progress is None or not self._item:
            return
        with toggle_timing.stage("progress"):
            self._progress(
                {**self._item, "total": self.total, "step": self._step, "fraction": round(self._fraction, 3)}
            )


def single(progress: ProgressCallback | None, current: int = 1, total: int = 1, build: float = 0.9) -> Part | None:
    """Report one build for an action the UI already names.

    Args:
        progress: Receives dictionaries with current, total, step, and fraction; None disables reporting.
        current: One-based position of the item in the enclosing job.
        total: Number of items in the enclosing job.
        build: Fraction of the item reserved for building before its write phase.

    Returns:
        The build's progress share, or None when reporting is disabled.
    """
    if progress is None:
        return None

    class _Plain(ItemProgress):
        def _emit(self) -> None:
            self._progress(
                {"current": current, "total": total, "step": self._step, "fraction": round(self._fraction, 3)}
            )

    owner = _Plain(progress, total)
    owner._item = {"current": current}
    return owner.part(0.0, 1.0, build=build)
