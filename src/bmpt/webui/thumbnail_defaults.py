"""The generated default thumbnails applied once after an upgrade, behind the ``thumbnails.*Default*`` RPC methods.

After an upgrade from an earlier version, every alt that still shows its default thumbnail gets its generated
default (the student list card's picture) as its App thumbnail and on the game's Collection portrait. The page
renders the pictures one at a time under the start cover, before the first screen, and stores them
(``thumbnails.storeDefault``); one start-up game writer job then applies them, one journaled write per shared
Collection group. The profile's marker is set only once a run
finished with nothing left, so an interrupted or failed run is tried again at the next start.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..core import preview_gate
from ..core.errors import ServiceError
from ..library.records import DefaultThumbnailsResult
from ..library.service.base import STOPPING
from bmpt.engine import get_version_info, t

from .jobs import START
from .state import UiState
from .tours import version_key

if TYPE_CHECKING:
    from .services import Services

logger = logging.getLogger(__name__)

KIND = "thumbnails.applyDefaults"
MARKER = "thumbnailDefaults"  # in the profile's tours history: "pending" or "done"


def decide(state: UiState, current: str) -> None:
    """Record, once per profile, whether the generated defaults are due: after an upgrade, never on fresh data.

    Called by ``thumbnails.defaults`` under the start cover, before the release notes record the launch
    (``tours.notes_launch``): the history still holds the previous launch's version then. A downgrade is no upgrade.

    Args:
        state: The profile's UI state.
        current: The running version.
    """
    current_key = version_key(current)
    if current_key is None:
        return

    def change(history: dict) -> None:
        if history.get(MARKER) in ("pending", "done"):
            return
        notes = history.get("notes") if isinstance(history.get("notes"), dict) else {}
        high = version_key(notes.get("highest")) or version_key(history.get("highest"))
        upgrade = (high is None and state.had_asset_consent) or (high is not None and current_key > high)
        history[MARKER] = "pending" if upgrade else "done"

    state.update_tours(change)


def pending(state: UiState) -> bool:
    """Whether the profile's generated defaults are still to be applied."""
    return state.tours().get(MARKER) == "pending"


def finish(state: UiState) -> None:
    """Record that the profile's generated defaults were applied."""

    def change(history: dict) -> None:
        history[MARKER] = "done"

    state.update_tours(change)


class ThumbnailDefaults:
    """Find, store and apply the generated default thumbnails of a profile's first start after an upgrade."""

    def __init__(self, services: Services):
        """Bind the operations to the services.

        Args:
            services: The Services: catalog, game, library, thumbnail store, jobs and UI state.
        """
        self.services = services

    def _blocked(self, library: Any) -> str | None:
        """Why the run waits for a later start, or None: it is skipped quietly then."""
        s = self.services
        if not s.game_found() or library is None or library.current_pin() is None:
            return "no game"
        if s.state.spine_consent is not True:
            return "Spine not accepted"
        if s.classic_open() and library.intent()["enabled"]:
            return "classic window open"
        return None

    def _targets(self, alts: list[str] | None = None) -> list[tuple[str, dict]]:
        """``(alt, descriptor)`` for the alts with a Collection portrait in the game, in catalog order."""
        s = self.services
        wanted = None if alts is None else set(alts)
        found = []
        for student in s.catalog.students():
            for alt in student.alts:
                name = getattr(alt, "official_thumbnail", None)
                if not name or (wanted is not None and alt.key not in wanted):
                    continue
                try:
                    found.append((alt.key, s.game.collection_target(name)))
                except (KeyError, ServiceError):
                    continue
        return found

    def candidates(self) -> dict:
        """List the alts whose generated default is due, while the run is due and may run now.

        Returns:
            ``due`` and ``candidates``, ``{alt, builtin}`` each: ``builtin`` is the alt's generated default's id
            when it has one already (nothing to render), else None. ``due`` is False and the list empty when the
            marker is set, or the run waits for a later start (no game, Spine not accepted, a classic window open).
        """
        s = self.services
        library = s.library
        if not getattr(s, "safe_mode", False):
            decide(s.state, get_version_info().get("version") or "")
        if not pending(s.state):
            return {"due": False, "candidates": []}
        reason = self._blocked(library)
        if reason:
            logger.info("Default thumbnails left for a later start: %s", reason)
            return {"due": False, "candidates": []}
        groups = library.default_thumbnail_groups(self._targets(), library.current_pin())
        store = s.thumbnail_store
        alts = [alt for items in groups.values() for alt, _descriptor in items]
        return {"due": True, "candidates": [{"alt": alt, "builtin": store.builtin(alt)} for alt in alts]}

    def store(self, alt: str, png: str) -> dict:
        """Store a rendered generated default without selecting or applying it.

        An alt that has one keeps it (``Thumbnails.save`` with ``builtin``).

        Args:
            alt: The alt key.
            png: The student list card's picture, as a PNG base64 data URL.

        Returns:
            ``saved``, the generated default's id.
        """
        s = self.services
        s.thumbnails._alt(alt)
        with s.thumbnail_writes:
            saved = s.thumbnail_store.save(alt, png, "portrait", source="generated", builtin=True)["saved"]
        return {"saved": saved}

    def apply(self, alts: list[str], complete: bool) -> str | None:
        """Apply the stored generated defaults of some alts, in a start-up game writer job.

        Args:
            alts: The alts whose generated default the page stored or found stored.
            complete: Whether the page rendered every candidate; without it the marker stays unset.

        Returns:
            The job's id; None when nothing is to be written, or the run waits for a later start. With nothing to
            write and ``complete``, the marker is set at once.
        """
        s = self.services
        if not isinstance(alts, list) or not all(isinstance(alt, str) for alt in alts):
            raise ServiceError("bad_params", key="alts")
        if not pending(s.state):
            return None
        library = s.library
        reason = self._blocked(library)
        if reason:
            logger.info("Default thumbnails left for a later start: %s", reason)
            return None
        if not alts:
            if complete:
                finish(s.state)
            return None
        with s.classic.starting():
            if s.classic_open() and library.intent()["enabled"]:
                return None
            return self._submit(library, library.current_pin(), alts, bool(complete))

    def _submit(self, library: Any, pin: Any, alts: list[str], complete: bool) -> str:
        """Queue the job that applies the alts' generated defaults, one write at a time.

        Class 3, under the start cover: one write holds the library lock for the shared bundle's store and
        journaled write (the bundle is composed with the lock given up); between writes it yields to the foreground
        previews (``preview_gate``) and to page reads (the lock is free), and hands what is left to a new job when
        one of a higher class waits (``next`` in the result, which the page waits for in turn). Its progress counts
        the writes.
        """
        s = self.services

        def run(log: Any, progress: Any) -> DefaultThumbnailsResult:
            s._check_pin(library, pin)
            groups = list(library.default_thumbnail_groups(self._targets(alts), pin).values())
            chosen, cores, failed = [], [], []
            label = t("webui.progress.defaultThumbnailsApply")
            for index, items in enumerate(groups):
                if index:
                    preview_gate.step(lambda: s.jobs.closing)
                    if s.jobs.outranked(START) and not s.jobs.closing:
                        left = [alt for rest in groups[index:] for alt, _descriptor in rest]
                        following = self._submit(library, pin, left, complete)
                        return DefaultThumbnailsResult(chosen, _cores(cores), failed, left, following)
                progress(index + 1, len(groups), label)
                if s.classic_open() and library.intent()["enabled"]:
                    failed.append({"alts": [alt for alt, _descriptor in items], "code": "classic_open"})
                    break
                try:
                    s._check_pin(library, pin)
                    selected = library.apply_default_thumbnails(items, pin, log=log)
                except ServiceError as error:
                    logger.info("Default thumbnails of %d alts not applied: %s", len(items), error.code)
                    failed.append({"alts": [alt for alt, _descriptor in items], "code": error.code})
                    if error.code in STOPPING:
                        break  # the same refusal for every write: the next start tries again
                    continue
                chosen.extend(selected)
                cores.append(items[0][1]["bundle"])
                for alt in selected:
                    s.events.publish("thumbnails.changed", {"alt": alt})
            if complete and not failed:
                finish(s.state)
            return DefaultThumbnailsResult(chosen, _cores(cores), failed)

        return s.submit(KIND, run, writes="game", library=library, rank=START)


def _cores(bundles: list[str]) -> list[str]:
    from ..library.judge import core_of

    return sorted({core_of(name) for name in bundles})


def register(rpc: Any, services: Services) -> None:
    """Register the ``thumbnails.defaults``, ``thumbnails.storeDefault`` and ``thumbnails.applyDefaults`` methods.

    Args:
        rpc: The ``RpcRegistry`` to register on.
        services: The Services whose ``thumbnail_defaults`` handles the calls.
    """

    @rpc.method("thumbnails.defaults")
    def defaults():
        return services.thumbnail_defaults.candidates()

    @rpc.method("thumbnails.storeDefault")
    def store_default(alt: str, png: str):
        return services.thumbnail_defaults.store(alt, png)

    @rpc.method("thumbnails.applyDefaults")
    def apply_defaults(alts: list[str], complete: bool = True):
        return services.thumbnail_defaults.apply(alts, complete)
