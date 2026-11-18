"""The ``/asset/...`` answers of the web UI's services, a mixin of ``Services``."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Mapping, Callable

from ..core import devmode, preview_gate, spans
from ..core.errors import ServiceError
from bmpt.engine import parse_filename
from ..core.openfiles import temporary_directory
from .jobs import Cancelled
from .contract import SPINE_TYPES
from ..host.store_formats import source_value
from .server import MIME, AssetResponse


logger = logging.getLogger("bmpt.webui.services")  # the services log under one name

# /asset/ kinds the shown page draws as previews (portraits, lobbies, backgrounds, 3D models): class 1 of the ordering
PREVIEW_ASSETS = frozenset({"spine", "art", "office", "chibi"})


class AssetsMixin:
    """The ``/asset/...`` answers of ``Services``."""

    def asset(self, path: str, query: dict[str, str]) -> AssetResponse:
        """Answer an ``/asset/...`` request.

        The first segment is the kind: ``photography``, ``ui``, ``office``, ``art``, ``spine``, ``model`` or
        ``voice``. Previews the page draws (``PREVIEW_ASSETS``) are class 1: scans and background work step aside
        until they are answered.

        Args:
            path: The path after ``/asset/``.
            query: The request's query parameters, such as a model's ``source`` and ``clip``.

        Returns:
            The asset.

        Raises:
            ServiceError: ``game_not_found`` in safe mode or without a game; others from the services.
            KeyError: The path names no asset, which is a 404.
        """
        if self.safe_mode:
            raise ServiceError("game_not_found")
        if path.split("/", 1)[0] in PREVIEW_ASSETS:
            # what the shown page draws: scans and background work step aside until it is answered
            with preview_gate.foreground():
                return self._asset(path, query)
        return self._asset(path, query)

    def _asset(self, path: str, query: dict[str, str]) -> AssetResponse:
        parts = path.split("/")
        kind = parts[0]
        if kind == "thumbnails":
            return self.thumbnails.asset(parts[1:])
        if kind == "ui":
            return self._ui_asset(parts[1:], query)
        if kind == "office" and len(parts) == 3 and parts[2] == "layout.json":
            return _json(self.game.office_layout(parts[1]), light=True)
        if kind == "office" and len(parts) == 4 and parts[2] == "texture" and parts[3].endswith(".png"):
            return AssetResponse(self.game.office_texture(parts[1], _stem(parts[3])), "image/png", light=False)
        if kind == "art" and len(parts) == 3:
            if parts[1] == "background":
                data, mime = self.game.collection_bg(_stem(parts[2]))
                return AssetResponse(data, mime, light=False)
            if parts[1] == "officialThumbnail":
                return AssetResponse(self.game.collection_portrait(_stem(parts[2])), "image/png", light=False)
        if kind == "spine" and len(parts) >= 5:
            return self._spine_asset(SPINE_TYPES[parts[1]], parts[2], "/".join(parts[4:]), revision=parts[3])
        if kind == "spine" and len(parts) == 4:
            return self._spine_asset(SPINE_TYPES[parts[1]], parts[2], parts[3])
        if kind == "chibi" and len(parts) == 2 and parts[1].endswith(".glb"):
            # A variant's 3D chibi, exported losslessly and cached only with separate preview consent.
            key = _stem(parts[1])
            field_only = False
            try:
                variant = self.catalog.variant(key)
                core, target = variant.model, variant.key
                if not core and getattr(variant, "field_model", None):
                    core, field_only = variant.field_model, True
            except (ServiceError, KeyError) as error:
                if isinstance(error, ServiceError) and error.code != "catalog_alt_not_found":
                    raise
                # NPC models can exist without a student-list entry. Resolve only
                # cores indexed from this game's own files, never arbitrary names.
                if key not in getattr(self.game, "model_cores", lambda: set())():
                    raise KeyError(path) from None
                core = target = key
            if not core:
                raise KeyError(path)
            source = source_value(query.get("source", "live"), forward=False)
            kind = source.split(":", 1)[0]
            # ?clip=<name>: one clip of the model, read from the same source as the model, for an action whose clips
            # the model's GLB does not carry. Class 1 (what the user is looking at): foreground like the model (asset
            # above), one step of about 0.5 s under the model lock, cached afterwards; a request the page gave up on
            # (the 3D tab left) lets its step finish and keeps the result.
            clip = query.get("clip") or None
            extra = {"clip": clip} if clip else {}
            # How long each preview source takes end to end: lock waits, CRC checks and the export.
            with spans.span(
                "asset.model."
                + ("clip." if clip else "")
                + (kind if kind in ("live", "official", "mod", "import", "variant") else "other")
            ):
                if field_only:  # no slot: its own files only, never a mod or another costume
                    if source not in ("live", "official"):
                        raise KeyError(path)
                    data = self.game.model_glb(core, **extra)
                else:
                    data = self._model_asset(core, target, source, path, clip)
            return AssetResponse(data, MIME[".glb"], light=False)
        if kind == "voice" and len(parts) == 3:
            # a memorial lobby voice clip from Kivo, relayed so it is never cached
            variant = self.catalog.variant(parts[1])
            return AssetResponse(self.kivo.audio(variant.page_id or "", _stem(parts[2])), "audio/ogg", light=False)
        raise KeyError(path)

    def costume_check(self, target: str, other: str, cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Check another variant's costume on a variant's model to its end, as the Switch 3D Model card shows it.

        The slot is built (``library.costume_preview``) and its preview exported. The verdict is saved for the
        pair, keyed by the build signature: the rule's refusals, program errors and export failures, so the pair
        is never offered. A refusal of the moment (a missing original, a locked file) is returned unsaved and
        checked again.

        Args:
            target: The variant whose model wears the costume.
            other: The variant whose costume it is.
            cancelled: The job's flag (``jobs.cancel``), checked between the phases (before the build, after it,
                before the export and before any verdict is saved); the build stops at its next step
                (``costume_preview``).

        Returns:
            The verdict: ``ok``, with ``preview`` for a costume past the stretch limit, or not ok with a
            ``reason``, and ``transient`` for one that is not saved.

        Raises:
            Cancelled: ``cancelled`` was set; no verdict is saved, so the pair is checked again next time.
        """

        def stop() -> None:
            if cancelled is not None and cancelled():
                raise Cancelled()

        library = self.library
        verdict = library.costume_verdict(target, other)
        if verdict is not None:
            return verdict
        core = self.catalog.variant(target).model
        stop()
        try:
            bundles = library.costume_preview(target, other, cancelled=cancelled)
        except ServiceError as error:
            stop()  # a build refused because the check was cancelled (busy) is no verdict either
            if error.code == "source_unsupported":
                return library.costume_verdict(target, other) or {"ok": False, "reason": error.params.get("source")}
            if error.code == "library_operation_failed":  # a program error, the same for these files every time
                return library.record_costume_verdict(target, other, False, error.code)
            return {"ok": False, "reason": error.code, "transient": True}
        # its export is one step under the model lock (about 1 s); it starts once no foreground preview waits
        preview_gate.step(cancelled)
        stop()
        try:
            self.game.model_glb(core, overrides=bundles, reads=library.preview_reads("official"))
        except ServiceError as error:
            return {"ok": False, "reason": error.code, "transient": True}
        except Exception as error:  # the preview cannot be drawn: a program error, kept in the log
            logger.exception("Costume preview export failed for %s <- %s", target, other)
            stop()
            return library.record_costume_verdict(target, other, False, "export", {"error": type(error).__name__})
        stop()
        # a costume past the stretch limit was saved by the preview as shown only
        return library.costume_verdict(target, other) or library.record_costume_verdict(target, other, True)

    def _model_asset(self, core: str, target: str, source: str, path: str, clip: str | None = None) -> bytes:
        """Export a variant's 3D chibi as GLB from one source.

        The source is the live game files, official copies, a library mod or an import candidate (KeyError, a 404,
        for anything else). Every source but ``live`` reads the files outside its own parts by the library's rule
        (``preview_reads``), never as the game folder holds them now.

        Args:
            core: The model's core.
            target: The variant the model belongs to.
            source: ``live``, ``official``, ``mod:<id>``, ``variant:<key>`` or ``import:<key>``.
            path: The asset path, for the KeyError.
            clip: One clip of that model instead, from the same files (a costume's adapted bundles included).
        """
        extra = {"clip": clip} if clip else {}
        if source == "live":
            return self.game.model_glb(core, **extra)
        if source == "official":
            return self.game.model_glb(
                core,
                overrides=self.library.official_bundles(core, current_fallback=True),
                reads=self.library.preview_reads("official"),
                **extra,
            )
        from ..library.judge import core_of, kind_of_file

        try:
            cores = set(getattr(self.library, "model_cores", lambda key: [core])(target))
        except ServiceError as error:
            if error.code != "catalog_alt_not_found" or target != core:
                raise
            cores = {core}  # An indexed NPC prefab can have no catalog variant.

        def fits(bundles: list[Path]) -> bool:
            return bool(bundles) and all(
                kind_of_file(bundle.name) == "model" and core_of(bundle.name) in cores for bundle in bundles
            )

        if source.startswith("mod:"):
            mod_id = source[4:]
            entry = next((mod for mod in self.library.mods() if mod.id == mod_id), None)
            if entry is None or entry.kind != "model" or entry.target != target:
                raise KeyError(path)
            bundles = self.library.preview_bundles(mod_id)
            if not fits(bundles):
                raise KeyError(path)
            return self.game.model_glb(core, overrides=bundles, reads=self.library.preview_reads("configured"), **extra)
        if source.startswith("variant:"):
            verdict = self.library.costume_verdict(target, source[8:])
            if verdict is not None and not verdict["ok"] and not verdict.get("preview"):
                raise KeyError(path)  # checked to its end and refused: never offered (costume_check)
            bundles = self.library.costume_preview(target, source[8:])
            if not fits(bundles):
                raise KeyError(path)
            return self.game.model_glb(core, overrides=bundles, reads=self.library.preview_reads("official"), **extra)
        if source.startswith("import:"):
            preview_core, bundles = self.batch_import.preview(source[7:])
            if preview_core not in cores or not fits(bundles):
                raise KeyError(path)
            return self.game.model_glb(core, overrides=bundles, reads=self.library.preview_reads("configured"), **extra)
        raise KeyError(path)

    def _ui_asset(self, parts: list[str], query: dict[str, str]) -> AssetResponse:
        game = self.game
        if parts == ["atlases.json"]:
            # Revalidate the bundle inventory on reload, including after a cache clear or a partial download.
            with self.timings.measure("ui.atlases"):
                return _json(game.atlases(), light=False)
        if parts == ["textures.json"]:
            with self.timings.measure("ui.textures"):
                return _json({name: f"/asset/ui/texture/{name}.png" for name in game.textures()}, light=True)
        if len(parts) == 3 and parts[0] == "sprite":
            return AssetResponse(game.sprite_png(parts[1], _stem(parts[2])), "image/png", light=True)
        if len(parts) == 2 and parts[0] == "texture":
            return AssetResponse(game.texture_png(_stem(parts[1])), "image/png", light=True)
        if len(parts) == 2 and parts[0] == "selection-frame":
            return AssetResponse(game.selection_frame_texture(_stem(parts[1])), "image/png", light=False)
        if len(parts) == 2 and parts[0] == "layout":
            root = _stem(parts[1])
            with self.timings.measure(f"ui.layouts.{root}"):
                return _json(game.layout(root, query.get("path", "")), light=True)
        raise KeyError("/".join(parts))

    def _install_game(self, install_id: str) -> Any:
        """Return the game files of another known install, by its id in ``mods.state.installs``.

        An entry card of that install draws its own files: a GameAssets over that install's pinned resource
        folders, built on first use and dropped with the services rebuild. KeyError (404) for an unknown id.
        """
        library = self.library
        pin = next((p for p in library.installs() if p.id == install_id), None)
        if pin is None:
            raise KeyError(install_id)
        current = library.current_pin()
        if current is not None and current.key == pin.key:
            return self.game
        with self._lock:
            game = self._other_games.get(pin.key)
            if game is None:
                game = self._other_games[pin.key] = self._make_game(list(pin.dirs), private=True)
            return game

    def _spine_bundles(self, kind: str, key: str) -> list[Path]:
        core, pin, game = key, None, self.game
        if "@" in key and kind not in ("mod", "import"):
            core, _, install_id = key.rpartition("@")
            game = self._install_game(install_id)
            pin = next(p for p in self.library.installs() if p.id == install_id)
        if kind.startswith("official-") and kind[9:] in ("spr", "lobby"):
            return self.library.official_bundles(core, pin=pin)
        if kind in ("spr", "lobby"):
            try:
                # the game files with the mod-carrying parts last, the order a mod preview merges in
                return self.library.live_bundles(core, pin=pin)
            except ServiceError as error:
                if error.code != "game_file_not_found":
                    logger.warning("Cannot resolve live Spine bundles for %s/%s: %r", kind, key, error)
                    raise
                return game.spine_cores(kind)[core.lower()]
        if kind == "mod":
            return self.library.preview_bundles(key)
        if kind == "import":
            return self.batch_import.preview(key)[1]
        raise KeyError(kind)

    def _spine_asset(self, kind: str, key: str, name: str, revision: str | None = None) -> AssetResponse:
        # How long each Spine request takes, by source and file kind; names never reach the timings.
        what = name if name in ("skeleton", "manifest.json", "framing.json", "lobby.json") else "file"
        source = kind if kind in ("spr", "lobby", "official-spr", "official-lobby", "mod", "import") else "other"
        with spans.span(f"asset.spine.{source}.{what}"):
            return self._spine_response(kind, key, name, revision)

    def _spine_response(self, kind: str, key: str, name: str, revision: str | None = None) -> AssetResponse:
        bundles = None
        if name == "manifest.json" or revision is not None or kind.startswith("official-"):
            bundles = self._spine_bundles(kind, key)
            current = self._revision(kind, key, bundles)
            if revision is not None and revision != current:
                raise KeyError(f"Spine revision changed: requested {revision}, current {current}")
            if name == "manifest.json":
                return _json({"revision": current}, light=False)
        response = self._spine_file(kind, key, name, bundles)
        # A switch between the skeleton and page requests must fail the load, never mix revisions. Also reject
        # changes during extraction, before any bytes from the changed composition reach the player.
        if bundles is not None and (
            self._revision(kind, key, bundles) != current or self._spine_bundles(kind, key) != bundles
        ):
            raise KeyError(f"Spine source changed during extraction: {kind}/{key}")
        return response

    def _spine_file(self, kind: str, key: str, name: str, bundles: list[Path] | None = None) -> AssetResponse:
        game, core, pin = self.game, key, None
        if kind in ("spr", "lobby", "official-spr", "official-lobby") and "@" in key:
            # <core>@<id>: the core in another known install (its id in mods.state.installs)
            core, _, install_id = key.rpartition("@")
            game = self._install_game(install_id)
            pin = next(p for p in self.library.installs() if p.id == install_id)
        if name == "framing.json":
            from ..gameassets import AssetNotFound, lobby

            camera = False
            if kind == "import":
                core = self.batch_import.preview(key)[0]
            elif kind == "mod":
                sources = bundles if bundles is not None else self.library.preview_bundles(key)
                core = parse_filename(sources[-1].name).core
                camera = getattr(self.library, "camera_prefab", lambda _id: None)(key) is not None
            try:
                # a lobby camera's still is framed as its lobby shows it: by its own prefab (its lobby.json), whose
                # layers the camera scaled and moved; any other mod by the official prefab
                info = self._camera_layout(game, key) if camera else game.lobby_info(core)
                layer = next((layer for layer in info["layers"] if layer["name"] == info.get("main")), None)
                layer = layer or next(iter(info["layers"]), lobby.STANDARD)
                return _json(
                    {"view": info["view"], "layer": {k: layer[k] for k in ("name", "x", "y", "scale") if k in layer}},
                    light=False,
                )
            except AssetNotFound:
                return _json({"view": lobby.GAME_VIEW, "layer": lobby.STANDARD}, light=False)
        if kind == "lobby" and name == "lobby.json":
            # the lobby's layout and interaction from its prefab and clip assets; no skeleton file has this name, and
            # it is answered without extracting (and PNG-encoding) the lobby's textures. While one of its cameras holds
            # the prefab in the game, from the official prefab: no other choice's preview shows a camera's framing
            base = getattr(self.library, "lobby_prefab_base", lambda *_args, **_kw: None)(core, pin=pin)
            return _json(game.lobby_info(core, prefab=base) if base is not None else game.lobby_info(core), light=False)
        if kind == "mod" and name == "lobby.json":
            # a lobby camera's layout: its own prefab; any other mod has none of its own
            return _json(self._camera_layout(game, key), light=False)
        if kind == "mod" and (
            name.startswith("fx_") and name.endswith(".png") or name.startswith("sfx_") and name.endswith(".wav")
        ):
            # A lobby camera's layout route is its own, so the lobby asks it for the effect textures and sounds its
            # lobby.json names: those of the lobby it frames, read from the game files like the lobby's own
            found = getattr(self.library, "camera_prefab", lambda _id: None)(key)
            if found is not None:
                return self._spine_file("lobby", found[0]["core"], name)
        if kind == "lobby" and name.startswith("fx_") and name.endswith(".png"):
            # a texture of the lobby's particle effects (lobby.json fx.textures), read from the game files; any other
            # name is one of the skeletons' own files
            from ..gameassets import AssetNotFound

            try:
                return AssetResponse(game.lobby_fx_texture(core, name[3:-4]), "image/png", light=False)
            except AssetNotFound:
                pass
        if kind == "lobby" and name.startswith("sfx_") and name.endswith(".wav"):
            # a sound the lobby's clips play (lobby.json sounds), decoded from the game files in memory; any other
            # name is one of the skeletons' own files
            from ..gameassets import AssetNotFound

            try:
                return AssetResponse(game.lobby_sound(core, name[4:-4]), "audio/wav", light=False)
            except AssetNotFound:
                pass
        if kind.startswith("official-"):
            files = game.spine_files_from(bundles)
        elif kind in ("spr", "lobby"):
            if bundles is None:
                try:
                    bundles = self.library.live_bundles(core, pin=pin)
                except ServiceError as error:
                    if error.code != "game_file_not_found":
                        raise
            files = game.spine_files_from(bundles) if bundles is not None else game.spine_files(kind, core)
        elif kind == "mod":
            bundles = bundles if bundles is not None else self.library.preview_bundles(key)
            files = self._drawn_as_made(key, game.spine_files_from(bundles))
            # the mod's own core names its skeleton (a lobby has more than one: the character and its background)
            core = parse_filename(bundles[-1].name).core
        elif kind == "import":
            core, sources = self.batch_import.preview(key)
            files = game.spine_files_from(bundles if bundles is not None else sources)
        else:
            raise KeyError(kind)
        if name == "files.json":
            return _json(sorted(files), light=False)
        data = files.get(name)
        if data is None and Path(name).stem == "skeleton":
            picked = skeleton_file(files, core, Path(name).suffix)
            data = files[picked] if picked else None
        if data is None:
            raise KeyError(name)
        return AssetResponse(data, MIME.get(Path(name).suffix, "application/octet-stream"), light=False)

    def _recorded_rig(self, mod_id: str, atlases: dict[str, bytes] | None = None) -> dict[str, dict]:
        library = self.library
        if not hasattr(library, "recorded_rig"):
            return {}
        return library.recorded_rig(mod_id, atlases) if atlases else library.recorded_rig(mod_id)

    def _revision(self, kind: str, key: str, bundles: list[Path]) -> str:
        """Return a Spine preview's revision: its composition's (``spine_revision``).

        For a mod it also covers the rigs its fingerprint recorded where there are any (``_drawn_as_made`` draws on
        them), so that a preview drawn on the official rig is drawn again on the recorded one.
        """
        from ..gameassets.spine import spine_revision

        revision = spine_revision(bundles)
        recorded = self._recorded_rig(key) if kind == "mod" else {}
        kept = getattr(self.library, "rig_stamp", lambda _id: "")(key) if kind == "mod" else ""
        if not recorded and not kept:
            return revision
        rigs = sorted(
            [name, entry.get("layout"), hashlib.sha1(entry["skeleton"].encode("ascii")).hexdigest()]
            for name, entry in recorded.items()
        )
        return hashlib.sha256(json.dumps([revision, rigs, kept]).encode()).hexdigest()

    def _drawn_as_made(self, mod_id: str, files: dict[str, bytes]) -> dict[str, bytes]:
        """Return a texture-only portrait mod's preview files on the rig its textures were drawn for.

        This applies where the game has repacked the atlas (``fingerprint.drawn_on``), so that the mod shows as it
        was made; its pages keep the names the recorded atlas gives them.
        """
        from ..gameassets import AssetNotFound
        from ..gameassets.spine import atlas_pages
        from ..library.fingerprint import drawn_on

        recorded = self._recorded_rig(
            mod_id, {name: data for name, data in files.items() if name.lower().endswith(".atlas")}
        )
        rig = drawn_on(recorded, files) if recorded else {}
        if not rig:
            return files
        result = {**files, **rig}
        pages = {Path(name).stem.lower(): data for name, data in files.items() if name.lower().endswith(".png")}
        for name, data in rig.items():
            if not name.lower().endswith(".atlas"):
                continue
            for page in atlas_pages(data):
                if page not in result:
                    content = pages.get(Path(page).stem.lower())
                    if content is None:
                        raise AssetNotFound(page)
                    result[page] = content
        return result

    def _camera_layout(self, game: Any, mod_id: str) -> dict:
        from ..library.lobby_camera import patch_prefab

        found = getattr(self.library, "camera_prefab", lambda _id: None)(mod_id)
        if found is None:
            raise KeyError("lobby.json")
        meta, own, official, timeline = found
        if official is None:
            return game.lobby_info(meta["core"], prefab=own)
        # the game replaced the prefab the camera was made on and the camera is not rebuilt yet: framed on the fly,
        # from the stored parameters
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
            output = Path(temporary) / official.name
            patch_prefab(
                official, output, meta["root"], meta["scale"], tuple(meta["offset"]), timeline=timeline, crc=False
            )
            return game.lobby_info(meta["core"], prefab=output)


def skeleton_file(files: Mapping[str, Any], core: str, suffix: str) -> str | None:
    """Pick the file that "skeleton.skel" or "skeleton.atlas" stands for.

    That is the skeleton named after the core, else the only one, else, for a set of several (ui_set holds 22 raid
    and world boss banners), the first by name; its atlas is the one of the same stem.

    Args:
        files: The files of the composition, by name.
        core: The core the preview is for.
        suffix: ``.skel`` or ``.atlas``.

    Returns:
        The file's name; None when none can be told.
    """
    stems = sorted(
        {Path(k).stem for k in files if k.endswith(".skel")} | {Path(k).stem for k in files if k.endswith(".atlas")}
    )
    named = [stem for stem in stems if stem.lower() == core.lower()]
    stem = (named or sorted(Path(k).stem for k in files if k.endswith(".skel")) or stems or [None])[0]
    if stem is not None and f"{stem}{suffix}" in files:
        return f"{stem}{suffix}"
    matches = [k for k in files if k.endswith(suffix)]
    return matches[0] if len(matches) == 1 else None


def _stem(name: str) -> str:
    for suffix in (".png", ".jpg", ".json", ".ogg", ".glb"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _json(data: Any, light: bool) -> AssetResponse:
    return AssetResponse(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json", light
    )
