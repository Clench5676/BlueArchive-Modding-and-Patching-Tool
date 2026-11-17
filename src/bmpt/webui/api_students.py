"""The RPC methods of the student screens: the roster, a student's page, its previews, lobby and 3D model."""

from __future__ import annotations

import logging
import threading
import time
import weakref
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

from ..lines import global_lines as globallines
from ..catalog.special import SPECIAL_PREFIX
from ..core.errors import ServiceError
from bmpt.engine import i18n_manager, parse_filename
from ..lines.kivo import clip_id
from ..library.storage import trailing_bytes
from .jobs import Cancelled
from .rpc import RpcRegistry, to_json
from .services import Services
from .state import ALIAS_LENGTH, ALIAS_MAX, alias_key, alias_text


logger = logging.getLogger("bmpt.webui.api")  # every RPC method logs under one name
# UI language -> lobby lines key (Global en, zh-TW, ko, th; ja from Kivo, else Global)
LINE_LANGS = {"zh-CN": "zh-CN", "zh-TW": "zh-TW", "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th"}
# seconds lobby.lines waits for the Global table (a download the first time); it answers with Kivo's lines after that,
# so a slow download never holds the page's connections (the table keeps coming in for the next lobby)
LINES_WAIT = 3.0


def clip_talks(talks: Any) -> dict[str, list[str]]:
    """Check and normalise the page's talk animations, ``{animation: [clip ids]}``.

    Args:
        talks: What the page sent; at most 64 animations are read, with at most 32 clips each.

    Returns:
        The normalised table; empty when ``talks`` is not a dict.
    """
    if not isinstance(talks, dict):
        return {}
    return {
        str(anim): [clip_id(str(c)) for c in ids[:32]]
        for anim, ids in list(talks.items())[:64]
        if isinstance(ids, list)
    }


def _background(fn: Any, *args: Any) -> Future:
    """Run fn on a daemon thread (a slow download never holds up quitting); its result as a future."""
    future: Future = Future()

    def run() -> None:
        try:
            future.set_result(fn(*args))
        except BaseException as e:  # handed to the caller
            future.set_exception(e)

    threading.Thread(target=run, name="lobby-lines", daemon=True).start()
    return future


def _url_bg(name: str | None) -> str | None:
    return f"/asset/art/bg/{name}" if name else None


def _url_portrait(name: str | None) -> str | None:
    return f"/asset/art/portrait/{name}.png" if name else None


def register(rpc: RpcRegistry, s: Services) -> None:
    """Register the student screens' RPC methods (see the RPC table in docs/webui/api.md).

    Args:
        rpc: The registry to add the methods to.
        s: The Services every method works on.
    """
    from .assigned_catalog import students_with_assignments, student_with_assignments, stored_asset_fields

    def visible_students() -> list[Any]:
        return students_with_assignments(s.catalog, s.library.assigned_targets())

    def visible_alt(key: str) -> Any:
        try:
            return s.catalog.variant(key)
        except (ServiceError, KeyError):
            if key in s.library.assigned_targets():
                return s.catalog.assignment_variant(key)
            raise

    def alias_target(student: Any) -> str:
        # A missing normal base never gives its nickname to a skin. A special-only student has no such base:
        # attach the aliases stored under its student key to one stable, catalog-ordered scene and persist that
        # choice.
        if student.variants and all(v.key.startswith(SPECIAL_PREFIX) for v in student.variants):
            return min(student.variants, key=lambda v: (v.order, v.key)).key
        return student.key

    def alt_aliases(students: list[Any] | None = None) -> dict[str, list[str]]:
        targets = {
            f.key: target
            for f in (visible_students() if students is None else students)
            if (target := alias_target(f)) != f.key
        }
        return s.state.aliases(targets)

    photo_failure: dict[str, str] = {}  # the error code of the last failed photograph lookup, logged once

    def alt_json(
        v: Any,
        mods: dict[str, list],
        favorites: set[str],
        recent: list[str],
        seen: set[str] | None,
        modified: dict[str, str] | None = None,
        custom: dict[str, list[str]] | None = None,
        roster_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        own = mods.get(v.key, [])
        photograph = None
        try:
            photographs = getattr(s, "photographs", None)
            if photographs is not None:
                photograph = photographs.app_url(v.key)
                if photograph is None and photographs.selection(v.key) == "official" and v.portrait:
                    pin = s.library.current_pin()
                    if pin is not None:
                        photograph = s.photography.default_url(v.key, pin)
            photo_failure.clear()
        except ServiceError as error:
            # the thumbnail choice is optional: the student keeps its default one, and the log says so once
            photograph = None
            if photo_failure.get("code") != error.code:
                photo_failure["code"] = error.code
                logger.warning("Photograph lookup failed (%s), students use their default thumbnails", error.code)
        return {
            **({"thumbnail": photograph} if photograph else {}),
            **stored_asset_fields(v, own),
            "custom": (custom or {}).get(v.key, []),
            "key": v.key,
            "fileId": v.file_id,
            "student": v.family,
            "names": v.names,
            "playable": v.playable,
            "aliases": v.aliases,  # the search matches them, the page never shows them (the CN server's names)
            "recruitable": v.recruitable,  # on the selected server; playable is on some server
            "order": v.order,
            "spr": v.spr,
            "lobby": v.lobby,
            "date": v.date,
            "model": v.model,
            # a model with no slot (an event or story alt with no model of its own: a field player or NPC):
            # previewed and offered as a costume
            "fieldModel": getattr(v, "field_model", None),
            "spineCategory": getattr(v, "spine_category", None),
            "bg": _url_bg(v.bg),
            "portrait": _url_portrait(v.portrait),
            "favorite": v.key in favorites,
            "mods": len({getattr(m, "repaired_from", "") or m.id for m in own}),
            "modNames": list(
                dict.fromkeys(
                    name
                    for m in own
                    for name in (
                        getattr(m, "alias", ""),
                        getattr(m, "label", ""),
                        getattr(m, "core", ""),
                        Path(getattr(m, "file", "")).name,
                    )
                    if name
                )
            ),
            "fresh": seen is not None and any(m.user_added and m.id not in seen for m in own),
            "modCounts": {
                kind: len({getattr(m, "repaired_from", "") or m.id for m in own if m.kind == kind})
                for kind in ("spr", "lobby")
            },
            **(
                {
                    "configured": roster_state["configured"][v.key],
                    "previewSources": roster_state["previews"].get(v.key, {}),
                }
                if roster_state is not None
                else {
                    "configured": configured_of(v.key),
                    "previewSources": preview_sources_of(v.key),
                    "selection": selection_of(v.key),
                }
            ),
            # when the newest of its own mods was added (ISO), for the roster's "recently added" sort
            "added": max((m.added for m in own if getattr(m, "added", None)), default=None),
            "recent": recent.index(v.key) if v.key in recent else None,
            # the portrait game file carries something other than the official portrait (a mod or another alt)
            "replaced": (modified or {}).get(v.key),
        }

    def mods_by_target(entries: list | None = None) -> dict[str, list]:
        out: dict[str, list] = {}
        for m in s.library.mods() if entries is None else entries:
            out.setdefault(m.target, []).append(m)
        return out

    @rpc.method("roster.list")
    @s.timings.measure("roster.data")
    def roster_list() -> dict[str, Any]:
        if not s.game_found():
            raise ServiceError("game_not_found")
        catalog_students = s.catalog.families()
        snapshot = s.library.roster_snapshot([v.key for f in catalog_students for v in f.variants])
        catalog_students = students_with_assignments(s.catalog, snapshot.get("assigned", []))
        mods, favorites, recent = mods_by_target(snapshot["mods"]), snapshot["favorites"], s.state.recent()
        modified, custom = snapshot["modified"], alt_aliases(catalog_students)
        previews = s.state.preview_snapshot(snapshot["install"]) if snapshot["install"] is not None else {}
        by_id = {mod.id: mod for mod in snapshot["mods"]}
        alts = {v.key: v for student in catalog_students for v in student.variants}
        snapshot["previews"] = {
            key: valid_preview_sources(key, sources, by_id, alts) for key, sources in previews.items()
        }
        seen = s.state.seen_mods()
        students = []
        for f in catalog_students:
            students.append(
                {
                    "key": f.key,
                    "base": f.names,
                    "aliases": f.aliases,
                    "school": f.school,
                    "alts": [
                        alt_json(v, mods, favorites, recent, seen, modified, custom, snapshot) for v in f.variants
                    ],
                }
            )
        return {"students": students}

    def model_files(core: str | None, state: dict) -> list[dict[str, Any]]:
        """All model slot parts, with the same Needs Fix and restore judgements as the file table."""
        parts = [part for part in state.get("files", []) if part["kind"] == "model"]
        if parts:
            return sorted(parts, key=lambda part: (part["date"] or "", part["file"]), reverse=True)
        try:
            index = getattr(s.game, "_model_index", None) if core else None
            names = index().get(core.lower(), []) if index else []
        except (ServiceError, OSError):
            return []
        out = []
        for name in names:
            path = Path(name)
            parsed = parse_filename(path.name)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            trailing = trailing_bytes(path)
            if trailing is None:
                continue
            out.append(
                {
                    "file": str(path),
                    "kind": "model",
                    "date": parsed.date,
                    "size": size,
                    "crc": "fixed" if trailing else "ok",
                }
            )
        return sorted(out, key=lambda part: (part["date"] or "", part["file"]), reverse=True)

    def configured_of(key: str) -> dict[str, str | None]:
        try:
            return s.library.configured(key)
        except ServiceError:
            return {"spr": None, "lobby": None}

    def selection_of(key: str) -> dict:
        try:
            return s.library.selection(key)
        except ServiceError:
            return {}

    def preview_source_valid(
        alt: str, kind: str, source: str, mods: dict | None = None, alts: dict | None = None
    ) -> bool:
        v = alts.get(alt) if alts is not None else visible_alt(alt)
        if v is None:
            return False
        core = getattr(v, kind, None)
        if source == "official":
            return bool(core)
        if source.startswith("mod:"):
            mod = (
                mods.get(source[4:])
                if mods is not None
                else next((m for m in s.library.mods() if m.id == source[4:]), None)
            )
            if mod is None or mod.kind != kind:
                return False
            if mod.target == alt:
                return True
            if not core:
                return False
            other = alts.get(mod.target) if alts is not None else visible_alt(mod.target)
        elif source.startswith("alt:") and kind == "spr":
            if not core:
                return False
            other = alts.get(source[4:]) if alts is not None else s.catalog.variant(source[4:])
        elif source.startswith("alt:") and kind == "model":
            # another alt's costume on this alt's model slot: any other model, the student's own or, chosen
            # through the switch's search, another student's, whose check did not refuse it (costume_check)
            other = alts.get(source[4:]) if alts is not None else s.catalog.variant(source[4:])
            if not (
                bool(core)
                and other is not None
                and other.key != v.key
                and bool(getattr(other, "model", None) or getattr(other, "field_model", None))
            ):
                return False
            verdict = getattr(s.library, "costume_verdict", lambda *args: None)(v.key, other.key)
            return verdict is None or bool(verdict["ok"]) or bool(verdict.get("preview"))
        else:
            return False
        return (
            other is not None
            and other.family == v.family
            and bool(getattr(other, kind, None))
            and (kind == "spr" or getattr(other, kind, None) == core)
        )

    def preview_sources_of(key: str) -> dict:
        pin = getattr(s.library, "current_pin", lambda: None)()
        if pin is None:
            return {}
        sources = s.state.preview_sources(pin.key, key)
        return valid_preview_sources(key, sources)

    def valid_preview_sources(key: str, sources: dict, mods: dict | None = None, alts: dict | None = None) -> dict:
        valid = {}
        for kind, source in sources.items():
            try:
                if preview_source_valid(key, kind, source, mods, alts):
                    valid[kind] = source
            except (ServiceError, KeyError):
                pass
        return valid

    def costume_keys(alt: str, others: Any) -> list[str]:
        if (
            not isinstance(alt, str)
            or not alt
            or not isinstance(others, list)
            or len(others) > 64
            or not all(isinstance(o, str) and o and len(o) <= 256 for o in others)
        ):
            raise ServiceError("bad_params", key="others")
        return list(dict.fromkeys(others))

    @rpc.method("model.costumes")
    def model_costumes(alt: str, others: list[str]) -> dict[str, str | None]:
        """Return the saved verdicts of other alts' costumes on an alt's model (Switch 3D Model).

        A verdict is "ok", "preview" (past the stretch limit: shown, and applied only for another student's
        costume), "refused", or None when not checked yet (``model.checkCostumes``). "ok" and "preview" pairs are
        offered; ``others`` may name any student's alts (the switch's search hits).
        """
        out: dict[str, str | None] = {}
        for other in costume_keys(alt, others):
            try:
                if not preview_source_valid(alt, "model", f"alt:{other}"):
                    out[other] = "refused"
                    continue
                verdict = s.library.costume_verdict(alt, other)
            except (ServiceError, KeyError):
                out[other] = "refused"
                continue
            out[other] = verdict_kind(verdict)
        return out

    @rpc.method("model.costumeCautions")
    def model_costume_cautions(alt: str, others: list[str]) -> dict[str, list[str]]:
        """Return what the offered pairs among ``others`` may draw differently in the game than in the preview.

        These are the switch's cautions (``model_adapt.Result.cautions``): ``{other: ["ex_parts" |
        "carried_shader", ...]}``, only the pairs that have any.
        """
        out: dict[str, list[str]] = {}
        for other in costume_keys(alt, others):
            try:
                verdict = s.library.costume_verdict(alt, other)
            except (ServiceError, KeyError):
                continue
            cautions = (verdict or {}).get("cautions")
            if verdict_kind(verdict) in ("ok", "preview") and isinstance(cautions, list) and cautions:
                out[other] = [c for c in cautions if isinstance(c, str)]
        return out

    def verdict_kind(verdict: dict | None) -> str | None:
        return (
            None if verdict is None else "ok" if verdict["ok"] else "preview" if verdict.get("preview") else "refused"
        )

    @rpc.method("model.checkCostumes")
    def model_check_costumes(alt: str, others: list[str]) -> str:
        """Start a job that checks other alts' costumes on an alt's model to their end.

        See ``Services.costume_check``; the result is ``{other: "ok" | "preview" | "refused"}``. A refusal of the
        moment is "refused" and not saved. Class 2, read-only; the page cancels it (``jobs.cancel``) once its 3D tab
        is left: a queued one never runs, a running one stops between its pairs and phases, saving no verdict (the
        "cancelled" state).
        """
        keys = costume_keys(alt, others)

        def run(log: Any, progress: Any, cancelled: Callable[[], bool]) -> dict[str, str]:
            out: dict[str, str] = {}
            for i, other in enumerate(keys):
                if cancelled():
                    raise Cancelled()
                if progress:
                    progress(i, len(keys), "")
                try:
                    verdict = (
                        s.costume_check(alt, other, cancelled=cancelled)
                        if preview_source_valid(alt, "model", f"alt:{other}")
                        else None
                    )
                except (ServiceError, KeyError):
                    verdict = None
                out[other] = verdict_kind(verdict) or "refused"
            return out

        return s.submit("costume-check", run, cancellable=True)

    @rpc.method("model.optimize")
    def model_optimize(alt: str, source: str, name: str = "") -> str:
        """Start a job that runs Optimize Physics on an alt's 3D model from one source.

        The source is "official" or "alt:<key>", another alt's costume. The result is ``{"mod": id}``, the
        copy kept in the library, or ``{"unneeded": true, "reason": "official" | "no_gain"}``. ``name`` is the source
        as the screen shows it, the copy's name.
        """
        if not isinstance(alt, str) or not alt or not isinstance(source, str) or not source or len(source) > 264:
            raise ServiceError("bad_params", key="source")
        if not isinstance(name, str) or len(name) > 200:
            raise ServiceError("bad_params", key="name")
        if source != "official" and not preview_source_valid(alt, "model", source):
            raise ServiceError("source_unsupported", source=source)

        library = s.library
        pin = s._current_pin(library)

        def run(log: Any, progress: Any) -> dict[str, Any]:
            progress({"current": 1, "total": 1, "step": ""})
            return library.optimize_costume(alt, source, name, pin=pin, progress=progress)

        return s.submit("model.optimize", run, writes="library", library=library)

    @rpc.method("student.get")
    @s.timings.measure("detail.data")
    def student_get(key: str) -> dict[str, Any]:
        f = student_with_assignments(s.catalog, s.library.assigned_targets(), key)
        mods, favorites, recent = mods_by_target(), s.library.favorites(), s.state.recent()
        seen = s.state.seen_mods()
        alts = []
        custom = alt_aliases()
        for v in f.variants:
            item = alt_json(v, mods, favorites, recent, seen, custom=custom)
            item["state"] = to_json(s.library.state(v.key))
            item["modList"] = [
                {**to_json(m), "fresh": seen is not None and m.user_added and m.id not in seen}
                for m in mods.get(v.key, [])
            ]
            for mod in item["modList"]:
                try:
                    if getattr(s.library, "preview_incomplete", lambda _: False)(mod["id"]):
                        mod["incomplete"] = True
                except ServiceError:
                    pass
            item["modelCores"] = getattr(s.library, "model_cores", lambda key: [v.model] if v.model else [])(v.key)
            item["modelFiles"] = model_files(v.model, item["state"])
            alts.append(item)
        # Include the installation: two servers can have the same resource hash.
        return {
            "key": f.key,
            "base": f.names,
            "aliases": f.aliases,
            "school": f.school,
            "alts": alts,
            "version": getattr(s.game, "cache_version", s.game.resource_version) if s.game_found() else None,
        }

    @rpc.method("aliases.set")
    def aliases_set(aliases: list[str], alt: str | None = None, student: str | None = None) -> list[str]:
        """Keep the user's own aliases of an alt, which the roster search matches, and return them as kept.

        Each is cleaned (``state.alias_text``), and empty and repeated ones are dropped. ``alias_too_long`` or
        ``alias_too_many`` past the limits; nothing is kept then.
        """
        if (
            not isinstance(alt if alt is not None else student, str)
            or not isinstance(aliases, list)
            or not all(isinstance(a, str) for a in aliases)
        ):
            raise ServiceError("bad_params", key="aliases")
        # The student argument edits the original alt only.
        key = (
            visible_alt(alt).key
            if alt is not None
            else alias_target(student_with_assignments(s.catalog, s.library.assigned_targets(), student))
        )
        names = [alias_text(a) for a in aliases]
        if any(len(name) > ALIAS_LENGTH for name in names):
            raise ServiceError("alias_too_long", max=ALIAS_LENGTH)
        if len({alias_key(name) for name in names if name}) > ALIAS_MAX:
            raise ServiceError("alias_too_many", max=ALIAS_MAX)
        alt_aliases()
        return s.state.set_aliases(key, names)

    warmed: dict[str, Any] = {"game": None}
    warm_lock = threading.Lock()

    @rpc.method("lobby.warm")
    def lobby_warm() -> None:
        """Warm the memorial prefab index once per game. CAB dependencies resolve on demand."""
        if not s.game_found():
            return
        game = s.game
        with warm_lock:
            if warmed["game"] is not None and warmed["game"]() is game:
                return
            warmed["game"] = weakref.ref(game)

        def build() -> None:
            try:
                game._lobby_prefabs()
            except Exception:  # the lobby builds them again when it needs them
                logger.debug("Cannot build the memorial lobby indexes", exc_info=True)

        threading.Thread(target=build, name="lobby-warm", daemon=True).start()

    @rpc.method("lobby.camera.save")
    def lobby_camera_save(
        alt: str, lobby: str, view: dict, scale: float, offset: list, source: str = "official"
    ) -> str:
        """Start a job that keeps the lobby's framing as a new Memorial Lobby choice of the alt.

        The framing is ``lobby.js cameraParams`` (``library/lobby_camera.py``): its art is the source the lobby
        showed ("official", ``mod:<id>`` of a lobby mod of the alt, or one of its cameras), its camera the
        prefab scaled by ``scale`` and moved by ``offset``. The result is ``{alt, lobby, number, scale, offset,
        mod}``. ``view`` is the framed view, for the log only.
        """
        from ..gameassets import AssetNotFound
        from ..library import lobby_camera

        v = visible_alt(alt) if isinstance(alt, str) else None
        if v is None or not v.lobby or lobby != v.lobby:
            raise ServiceError("bad_params", key="lobby")
        if (
            not isinstance(source, str)
            or len(source) > 264
            or not (source == "official" or preview_source_valid(alt, "lobby", source))
        ):
            raise ServiceError("source_unsupported", source=str(source)[:264])
        if not isinstance(view, dict) or not all(lobby_camera._number(view.get(key)) for key in ("x", "y", "w", "h")):
            raise ServiceError("bad_params", key="view")
        k, (ox, oy) = lobby_camera.check_params(scale, offset)
        finder = getattr(s.game, "lobby_prefab", None) if s.game_found() else None
        try:
            found = finder(v.lobby) if finder is not None else None
        except (AssetNotFound, OSError, ValueError) as error:
            raise ServiceError("lobby_camera_unsupported", name=v.lobby) from error
        if found is None:
            raise ServiceError("lobby_camera_unsupported", name=v.lobby)
        prefab, root = found
        library = s.library
        pin = s._current_pin(library)

        def run(log: Any, progress: Any) -> dict[str, Any]:
            progress({"current": 1, "total": 1, "step": ""})
            log(f"Lobby camera of {alt}: view {view}, scale {k:.4f}, offset {ox:.1f}, {oy:.1f}")
            record = library.save_camera(alt, source, k, [ox, oy], prefab, root, pin=pin, log=log)
            meta = record[lobby_camera.TAG]
            return {
                "alt": alt,
                "lobby": v.lobby,
                "number": meta["number"],
                "scale": meta["scale"],
                "offset": meta["offset"],
                "mod": record["id"],
            }

        return s.submit("lobby.camera.save", run, writes="library", library=library)

    @rpc.method("lobby.lines")
    def lobby_lines(alt: str, talks: dict[str, list[str]] | None = None, lang: str | None = None) -> dict[str, Any]:
        """Return the memorial lobby's lines by voice clip, ``{clip: {lang: text, "voice": bool}}``.

        The lines are Kivo's (zh-CN, ja, the voices) and, for a ``lang`` other than zh-CN, the Global server's
        official text on the clips of each talk animation (``talks``: ``{animation: [clip ids in order]}`` from the
        skeleton, else Kivo's clip names). ``lines_unavailable`` when neither can be read; the Global text is left
        out when its table takes longer than ``LINES_WAIT``.
        """
        v = visible_alt(alt)
        lang = lang or LINE_LANGS.get(i18n_manager.lang, "en")
        character = getattr(v, "schale_id", None)
        # the Global table (a download the first time) is read while Kivo's page is
        until = time.monotonic() + LINES_WAIT
        pending = _background(s.global_lines.dialogues, character) if lang != "zh-CN" and character else None
        try:
            kivo = s.kivo.lines(v.page_id or "")
        except ServiceError:
            kivo = None
        official: dict[str, dict[str, str]] = {}
        if pending is not None:
            try:
                table = pending.result(timeout=max(0.0, until - time.monotonic()))
                official = globallines.by_clip(table, clip_talks(talks) or globallines.talks_of(kivo or {}))
            except FutureTimeout:
                logger.info("Global lobby lines not ready after %.0f s: only Kivo's for %s", LINES_WAIT, alt)
            except ServiceError:
                pass
        if kivo is None and not official:
            raise ServiceError("lines_unavailable")
        return globallines.merge(kivo or {}, official)

    @rpc.method("favorites.set")
    def favorites_set(alt: str, on: bool) -> None:
        s.library.set_favorite(alt, bool(on))
        s.events.publish("library.changed")

    @rpc.method("ui.visit")
    def ui_visit(alt: str) -> None:
        s.state.visit(alt)

    @rpc.method("ui.preview")
    def ui_preview(alt: str, kind: str, source: str) -> dict:
        if not all(isinstance(value, str) for value in (alt, kind, source)) or kind not in (
            "spr",
            "lobby",
            "model",
        ):
            raise ServiceError("bad_params", key="source")
        if not preview_source_valid(alt, kind, source):
            raise ServiceError("bad_params", key="source")
        pin = s.library.current_pin()
        if pin is None:
            raise ServiceError("game_not_found")
        s.state.set_preview_source(pin.key, alt, kind, source)
        return s.state.preview_sources(pin.key, alt)
