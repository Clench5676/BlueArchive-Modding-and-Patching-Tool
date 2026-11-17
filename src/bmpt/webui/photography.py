"""Student photography RPCs and assets backed by the shared Mod library."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from ..core.errors import ServiceError
from ..core import devmode
from ..library.photographs import decode_capture
from ..library.storage import sync_directory
from .system import reveal
from ..library.judge import core_of
from .server import AssetResponse

if TYPE_CHECKING:
    from .services import Services


def _create_copy(source: Path, target: Path) -> None:
    """Create target exclusively and copy source into it; a partial copy is removed."""
    with open(target, "xb") as out:
        try:
            with open(source, "rb") as data:
                shutil.copyfileobj(data, out)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            out.close()
            target.unlink(missing_ok=True)
            raise


class Photography:
    """The student photographs behind the ``photography.*`` RPC methods and ``/asset/photography``.

    Photographs are captures of a student's portrait or lobby that the Mod library stores. One can be the App's own
    thumbnail of the student, and one can be applied to the game's Collection portrait.
    """

    def __init__(self, services: Services):
        """Bind the operations to the services.

        Args:
            services: The Services: catalog, library, photograph store, jobs and settings.
        """
        self.services = services

    def _variant(self, key: str):
        if not isinstance(key, str) or len(key) > 200:
            raise ServiceError("bad_params", key="variant")
        try:
            return self.services.catalog.variant(key)
        except (KeyError, ServiceError) as error:
            if isinstance(error, ServiceError) and error.code != "catalog_variant_not_found":
                raise
            resolve = getattr(self.services.library, "variant_metadata", None)
            if callable(resolve):
                return resolve(key)
            raise

    def _target(self, key: str) -> dict:
        variant = self._variant(key)
        if not getattr(variant, "portrait", None):
            raise ServiceError("photo_target_unavailable")
        return self.services.game.collection_target(variant.portrait)

    def default_url(self, variant: str, pin) -> str:
        """Return the asset URL of a variant's official Collection portrait.

        Args:
            variant: The variant key.
            pin: The game install and server the operation is fixed to; its id is part of the URL, and the game's
                resource version ends it so that a game update loads the new picture.
        """
        revision = self.services.game.resource_version
        return f"/asset/photography/default/{pin.id}/{quote(variant, safe='')}.png?revision={revision}"

    def describe(self, result: dict) -> dict:
        """Add the names of the student to each photo of a listing.

        Args:
            result: A photograph store listing. Each photo gets ``names`` from the catalog, or ``{}`` for a variant
                the catalog and library do not know.

        Returns:
            The same listing.
        """
        for entry in result["photos"]:
            try:
                entry["names"] = self._variant(entry["variant"]).names
            except (KeyError, ServiceError):
                entry["names"] = {}
        return result

    def list(self, variant: str) -> dict:
        """List the photographs of a student's family and what the game's Collection portrait shows.

        Args:
            variant: The variant key.

        Returns:
            The store's listing, in which each photo says whether it is ``applied`` to the game, plus ``game`` (the
            Collection portrait's choice: ``id``, with ``official`` for the official art, and ``known``),
            ``default`` (the official portrait's asset URL) and ``applicable``. ``default`` is None and
            ``applicable`` False when there is no game or the variant has no Collection portrait.

        Raises:
            ServiceError: ``bad_params`` for a malformed key; ``catalog_variant_not_found`` for an unknown variant.
        """
        self._variant(variant)
        s = self.services
        result = self.describe(s.photographs.list(variant))
        try:
            pin = s.library.current_pin()
            if pin is None:
                raise ServiceError("game_not_found")
            descriptor = self._target(variant)
            game = s.library.photograph_choice(descriptor, pin)
            default = self.default_url(variant, pin)
            game = dict(game)
            key = game.get("id")
            if not key and game.get("known"):
                game["id"] = "official"
        except ServiceError as error:
            if error.code not in ("photo_target_unavailable", "game_not_found"):
                raise
            game, default = {"id": None, "known": False}, None
        # Applied describes this skin's Collection target. Deletion checks independently include every target.
        for entry in result["photos"]:
            entry["applied"] = game.get("known", False) and entry["id"] == game.get("id")
        return {**result, "game": game, "default": default, "applicable": default is not None}

    def save(self, variant: str, png: str, mode: str, expression: str, action: str, source: str) -> dict:
        """Store a capture in the library without selecting or applying it.

        Args:
            variant: The variant key the capture belongs to.
            png: The capture as a PNG base64 data URL.
            mode: The captured scene, ``portrait`` or ``lobby``.
            expression: The captured expression's label; empty when unknown.
            action: The captured animation's label; empty when unknown.
            source: The captured source's label; empty when unknown.

        Returns:
            The family's listing, which includes the new photo.

        Raises:
            ServiceError: ``photo_invalid`` when ``png`` is not an acceptable PNG.
        """
        self._variant(variant)
        # Persistence must not wait for an unrelated game writer's status scan, nor hold the services lock: the
        # manifest has its own lock, and the only thing to keep out is a library move (photograph_writes), which
        # is taken first so the store is resolved after a move that is under way.
        with self.services.photograph_writes:
            result = self.services.photographs.save(variant, png, mode, expression, action, source)
        return self.describe(result)

    def select(self, variant: str, key: str | None) -> dict:
        """Choose the photograph the App shows as the student's thumbnail and publish ``photography.changed``.

        Args:
            variant: The variant key.
            key: The photo's id, ``official`` for the game's art, or None for the default thumbnail.

        Returns:
            The family's listing with the new choice.
        """
        self._variant(variant)
        with self.services.photograph_writes:
            result = self.services.photographs.select(variant, key)
        self.services.events.publish("photography.changed", {"variant": variant})
        return self.describe(result)

    def removal_info(self, variant: str, key: str) -> dict:
        """Say which skins' managed recipes use a photograph, before it is deleted.

        Args:
            variant: The variant key.
            key: The photo's id, or ``official``, which is never used and cannot be deleted.

        Returns:
            ``official``, ``used``, ``used_here``, ``used_elsewhere`` and the sorted ``targets`` (variant keys).
        """
        self._variant(variant)
        return self.services.library.photograph_removal_info(variant, key)

    def delete(self, variant: str, key: str, confirm: bool = False) -> str:
        """Delete a photograph in a library writer job and publish ``photography.changed``.

        Args:
            variant: The variant key.
            key: The photo's id.
            confirm: Whether recipes that use the photo may be reset before it goes. Without it, a photo in use is
                refused (``asset_delete_confirm``, raised by the job).

        Returns:
            The job's id; its result is the family's remaining listing.

        Raises:
            ServiceError: ``asset_official`` for the official art, which cannot be deleted.
        """
        self._variant(variant)
        if key == "official":
            raise ServiceError("asset_official")
        s, library = self.services, self.services.library

        def run(log, progress):
            library.delete_photograph(variant, key, confirm=confirm, log=log)
            s.events.publish("photography.changed", {"variant": variant})
            return self.list(variant)

        return s.submit("photography.delete", run, writes="library", library=library)

    def apply(self, variant: str, key: str | None, *, png: str | None = None, restore_app: bool = False) -> str:
        """Write a photograph into the game's Collection portrait of a student, in a game writer job.

        Args:
            variant: The variant key.
            key: The photo's id; ``generated`` saves ``png`` as the variant's built-in default photo and applies
                that; ``official`` or None puts the official art back.
            png: The capture for ``generated``, as a PNG base64 data URL.
            restore_app: Also make the photo the App's thumbnail of the student.

        Returns:
            The job's id; the job kind is ``restore`` for the official art and ``apply`` otherwise.

        Raises:
            ServiceError: ``photo_target_unavailable`` when the variant has no Collection portrait;
                ``photo_invalid`` for a bad ``png``; ``classic_open`` while a classic window is open and mods are
                enabled.
        """
        self._variant(variant)
        s, library = self.services, self.services.library
        pin = s._current_pin(library)
        descriptor = self._target(variant)
        if key == "generated":
            decode_capture(png)
        elif key == "official":
            key = None

        def run(log, progress):
            s._check_pin(library, pin)
            chosen = key
            if chosen == "generated":
                chosen = library.photographs.save(variant, png, "portrait", source="generated", builtin=True)["saved"]
            result = library.apply_photograph(variant, chosen, descriptor, pin=pin, log=log, progress=progress)
            if restore_app:
                library.photographs.select(variant, chosen)
                s.events.publish("photography.changed", {"variant": variant})
            return replace(result, variant=variant)

        with s.classic.starting():
            if s.classic_open() and library.intent()["enabled"]:
                raise ServiceError("classic_open")
            return s.submit("restore" if key is None else "apply", run, writes="game", library=library)

    def restore(self, variant: str, png: str | None = None) -> str:
        """Put a student's default thumbnail back, in the game and in the App.

        The default is the variant's built-in photo, which ``png`` creates when the variant has none yet; without
        either, the official art. A variant with no Collection portrait only has its App thumbnail reset.

        Args:
            variant: The variant key.
            png: A capture to make the built-in default of a variant that has none, as a PNG base64 data URL.

        Returns:
            The job's id.
        """
        try:
            self._target(variant)
        except ServiceError as error:
            if error.code != "photo_target_unavailable":
                raise
            return self.services.jobs.submit("photography.restore", lambda log, progress: self.select(variant, None))
        existing = next(
            (
                entry["id"]
                for entry in self.services.photographs.list(variant)["photos"]
                if entry["variant"] == variant and entry.get("builtin")
            ),
            None,
        )
        return self.apply(variant, existing or ("generated" if png else None), png=png, restore_app=True)

    def export(self, variant: str, key: str | None, png: str | None = None) -> dict:
        """Save a photograph as a PNG in the output folder and show it in the file manager.

        An existing file is never overwritten; the name gets a counter instead.

        Args:
            variant: The variant key.
            key: The photo's id; ``generated`` exports ``png``; ``official`` or None exports the game's official
                portrait.
            png: The capture for ``generated``, as a PNG base64 data URL.

        Returns:
            ``path`` of the file and ``folder`` that holds it.

        Raises:
            ServiceError: ``game_not_found`` when the official art is wanted without a game; ``photo_invalid``
                for a bad ``png``.
        """
        self._variant(variant)
        if key == "generated":
            raw, _, _ = decode_capture(png)
        elif key in (None, "official"):
            pin = self.services.library.current_pin()
            if pin is None:
                raise ServiceError("game_not_found")
            raw = self.asset(["default", pin.id, variant + ".png"]).body
        else:
            raw = self.services.photographs.image(variant, key)
        folder = Path(self.services.settings.get("output_dir")).expanduser().resolve()
        devmode.check_write(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^a-zA-Z0-9_-]", "_", variant)[:100] + "-" + (key or "official")
        fd, name = tempfile.mkstemp(prefix=".photo-export-", dir=folder)
        staged = Path(name)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            counter = 0
            while True:
                target = folder / (stem + (f"-{counter}" if counter else "") + ".png")
                try:
                    try:
                        os.link(staged, target)
                    except FileExistsError:
                        raise
                    except OSError:
                        # Volumes without hard links (exFAT, FAT32, some shares) take an exclusive copy.
                        _create_copy(staged, target)
                    break
                except FileExistsError:
                    counter += 1
            sync_directory(folder)
        finally:
            staged.unlink(missing_ok=True)
        reveal(folder)
        return {"path": str(target), "folder": str(folder)}

    def asset(self, parts: list[str]) -> AssetResponse:
        """Serve a photograph for ``/asset/photography/...``.

        Args:
            parts: The path after ``photography/``, split at slashes: ``[default, <install id>, <variant>.png]``
                for the official Collection portrait read from the game files, or ``[<variant>, <photo id>.png]``
                for a saved photo.

        Returns:
            The PNG, which the web view must not cache.

        Raises:
            ServiceError: ``folder_changed`` when the install id is not the current install's.
            KeyError: ``parts`` is neither form.
        """
        s = self.services
        if len(parts) == 3 and parts[0] == "default" and parts[2].endswith(".png"):
            pin = s.library.current_pin()
            if pin is None or pin.id != parts[1]:
                raise ServiceError("folder_changed")
            variant = parts[2][:-4]
            descriptor = self._target(variant)
            paths = s.library.official_bundles(core_of(descriptor["bundle"]), pin)
            paths = [path for path in paths if path.name.removesuffix(".backup") == descriptor["bundle"]]
            return AssetResponse(s.game.collection_portrait_from(descriptor["name"], paths), "image/png", False)
        if len(parts) == 2 and parts[1].endswith(".png"):
            variant, key = parts[0], parts[1][:-4]
            self._variant(variant)
            return AssetResponse(s.photographs.image(variant, key), "image/png", False)
        raise KeyError("photography")


def register(rpc, services: Services) -> None:
    """Register the ``photography.*`` RPC methods.

    Args:
        rpc: The ``RpcRegistry`` to register on.
        services: The Services whose ``photography`` handles the calls.
    """

    @rpc.method("photography.list")
    def listing(variant: str):
        return services.photography.list(variant)

    @rpc.method("photography.save")
    def save(variant: str, png: str, mode: str, expression: str = "", action: str = "", source: str = ""):
        return services.photography.save(variant, png, mode, expression, action, source)

    @rpc.method("photography.select")
    def select(variant: str, id: str | None):
        return services.photography.select(variant, id)

    @rpc.method("photography.delete")
    def delete(variant: str, id: str, confirm: bool = False):
        return services.photography.delete(variant, id, confirm=confirm)

    @rpc.method("photography.removalInfo")
    def removal_info(variant: str, id: str):
        return services.photography.removal_info(variant, id)

    @rpc.method("photography.apply")
    def apply(variant: str, id: str | None, png: str | None = None):
        return services.photography.apply(variant, id, png=png)

    @rpc.method("photography.restore")
    def restore(variant: str, png: str | None = None):
        return services.photography.restore(variant, png)

    @rpc.method("photography.export")
    def export(variant: str, id: str | None, png: str | None = None):
        return services.photography.export(variant, id, png)
