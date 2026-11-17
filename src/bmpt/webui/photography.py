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

    def _alt(self, key: str):
        if not isinstance(key, str) or len(key) > 200:
            raise ServiceError("bad_params", key="alt")
        try:
            return self.services.catalog.variant(key)
        except (KeyError, ServiceError) as error:
            if isinstance(error, ServiceError) and error.code != "catalog_alt_not_found":
                raise
            resolve = getattr(self.services.library, "variant_metadata", None)
            if callable(resolve):
                return resolve(key)
            raise

    def _target(self, key: str) -> dict:
        alt = self._alt(key)
        if not getattr(alt, "portrait", None):
            raise ServiceError("photo_target_unavailable")
        return self.services.game.collection_target(alt.portrait)

    def default_url(self, alt: str, pin) -> str:
        """Return the asset URL of an alt's official Collection portrait.

        Args:
            alt: The alt id.
            pin: The game install and server the operation is fixed to; its id is part of the URL, and the game's
                resource version ends it so that a game update loads the new picture.
        """
        revision = self.services.game.resource_version
        return f"/asset/photography/default/{pin.id}/{quote(alt, safe='')}.png?revision={revision}"

    def describe(self, result: dict) -> dict:
        """Add the names of the student to each photo of a listing.

        Args:
            result: A photograph store listing. Each photo gets ``names`` from the catalog, or ``{}`` for an alt
                the catalog and library do not know.

        Returns:
            The same listing.
        """
        for entry in result["photos"]:
            try:
                entry["names"] = self._alt(entry["alt"]).names
            except (KeyError, ServiceError):
                entry["names"] = {}
        return result

    def list(self, alt: str) -> dict:
        """List the photographs of a student and what the game's Collection portrait shows.

        Args:
            alt: The alt id.

        Returns:
            The store's listing, in which each photo says whether it is ``applied`` to the game, plus ``game`` (the
            Collection portrait's choice: ``id``, with ``official`` for the official art, and ``known``),
            ``default`` (the official portrait's asset URL) and ``applicable``. ``default`` is None and
            ``applicable`` False when there is no game or the alt has no Collection portrait.

        Raises:
            ServiceError: ``bad_params`` for a malformed key; ``catalog_alt_not_found`` for an unknown alt.
        """
        self._alt(alt)
        s = self.services
        result = self.describe(s.photographs.list(alt))
        try:
            pin = s.library.current_pin()
            if pin is None:
                raise ServiceError("game_not_found")
            descriptor = self._target(alt)
            game = s.library.photograph_choice(descriptor, pin)
            default = self.default_url(alt, pin)
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

    def save(self, alt: str, png: str, mode: str, face: str, action: str, source: str) -> dict:
        """Store a capture in the library without selecting or applying it.

        Args:
            alt: The alt id the capture belongs to.
            png: The capture as a PNG base64 data URL.
            mode: The captured scene, ``portrait`` or ``lobby``.
            face: The captured face's label; empty when unknown.
            action: The captured animation's label; empty when unknown.
            source: The captured source's label; empty when unknown.

        Returns:
            The student's listing, which includes the new photo.

        Raises:
            ServiceError: ``photo_invalid`` when ``png`` is not an acceptable PNG.
        """
        self._alt(alt)
        # Persistence must not wait for an unrelated game writer's status scan, nor hold the services lock: the
        # manifest has its own lock, and the only thing to keep out is a library move (photograph_writes), which
        # is taken first so the store is resolved after a move that is under way.
        with self.services.photograph_writes:
            result = self.services.photographs.save(alt, png, mode, face, action, source)
        return self.describe(result)

    def select(self, alt: str, key: str | None) -> dict:
        """Choose the photograph the App shows as the student's thumbnail and publish ``photography.changed``.

        Args:
            alt: The alt id.
            key: The photo's id, ``official`` for the game's art, or None for the default thumbnail.

        Returns:
            The student's listing with the new choice.
        """
        self._alt(alt)
        with self.services.photograph_writes:
            result = self.services.photographs.select(alt, key)
        self.services.events.publish("photography.changed", {"alt": alt})
        return self.describe(result)

    def removal_info(self, alt: str, key: str) -> dict:
        """Say which skins' managed recipes use a photograph, before it is deleted.

        Args:
            alt: The alt id.
            key: The photo's id, or ``official``, which is never used and cannot be deleted.

        Returns:
            ``official``, ``used``, ``used_here``, ``used_elsewhere`` and the sorted ``targets`` (alt keys).
        """
        self._alt(alt)
        return self.services.library.photograph_removal_info(alt, key)

    def delete(self, alt: str, key: str, confirm: bool = False) -> str:
        """Delete a photograph in a library writer job and publish ``photography.changed``.

        Args:
            alt: The alt id.
            key: The photo's id.
            confirm: Whether recipes that use the photo may be reset before it goes. Without it, a photo in use is
                refused (``asset_delete_confirm``, raised by the job).

        Returns:
            The job's id; its result is the student's remaining listing.

        Raises:
            ServiceError: ``asset_official`` for the official art, which cannot be deleted.
        """
        self._alt(alt)
        if key == "official":
            raise ServiceError("asset_official")
        s, library = self.services, self.services.library

        def run(log, progress):
            library.delete_photograph(alt, key, confirm=confirm, log=log)
            s.events.publish("photography.changed", {"alt": alt})
            return self.list(alt)

        return s.submit("photography.delete", run, writes="library", library=library)

    def apply(self, alt: str, key: str | None, *, png: str | None = None, restore_app: bool = False) -> str:
        """Write a photograph into the game's Collection portrait of a student, in a game writer job.

        Args:
            alt: The alt id.
            key: The photo's id; ``generated`` saves ``png`` as the alt's built-in default photo and applies
                that; ``official`` or None puts the official art back.
            png: The capture for ``generated``, as a PNG base64 data URL.
            restore_app: Also make the photo the App's thumbnail of the student.

        Returns:
            The job's id; the job kind is ``restore`` for the official art and ``apply`` otherwise.

        Raises:
            ServiceError: ``photo_target_unavailable`` when the alt has no Collection portrait;
                ``photo_invalid`` for a bad ``png``; ``classic_open`` while a classic window is open and mods are
                enabled.
        """
        self._alt(alt)
        s, library = self.services, self.services.library
        pin = s._current_pin(library)
        descriptor = self._target(alt)
        if key == "generated":
            decode_capture(png)
        elif key == "official":
            key = None

        def run(log, progress):
            s._check_pin(library, pin)
            chosen = key
            if chosen == "generated":
                chosen = library.photographs.save(alt, png, "portrait", source="generated", builtin=True)["saved"]
            result = library.apply_photograph(alt, chosen, descriptor, pin=pin, log=log, progress=progress)
            if restore_app:
                library.photographs.select(alt, chosen)
                s.events.publish("photography.changed", {"alt": alt})
            return replace(result, variant=alt)

        with s.classic.starting():
            if s.classic_open() and library.intent()["enabled"]:
                raise ServiceError("classic_open")
            return s.submit("restore" if key is None else "apply", run, writes="game", library=library)

    def restore(self, alt: str, png: str | None = None) -> str:
        """Put a student's default thumbnail back, in the game and in the App.

        The default is the alt's built-in photo, which ``png`` creates when the alt has none yet; without
        either, the official art. An alt with no Collection portrait only has its App thumbnail reset.

        Args:
            alt: The alt id.
            png: A capture to make the built-in default of an alt that has none, as a PNG base64 data URL.

        Returns:
            The job's id.
        """
        try:
            self._target(alt)
        except ServiceError as error:
            if error.code != "photo_target_unavailable":
                raise
            return self.services.jobs.submit("photography.restore", lambda log, progress: self.select(alt, None))
        existing = next(
            (
                entry["id"]
                for entry in self.services.photographs.list(alt)["photos"]
                if entry["alt"] == alt and entry.get("builtin")
            ),
            None,
        )
        return self.apply(alt, existing or ("generated" if png else None), png=png, restore_app=True)

    def export(self, alt: str, key: str | None, png: str | None = None) -> dict:
        """Save a photograph as a PNG in the output folder and show it in the file manager.

        An existing file is never overwritten; the name gets a counter instead.

        Args:
            alt: The alt id.
            key: The photo's id; ``generated`` exports ``png``; ``official`` or None exports the game's official
                portrait.
            png: The capture for ``generated``, as a PNG base64 data URL.

        Returns:
            ``path`` of the file and ``folder`` that holds it.

        Raises:
            ServiceError: ``game_not_found`` when the official art is wanted without a game; ``photo_invalid``
                for a bad ``png``.
        """
        self._alt(alt)
        if key == "generated":
            raw, _, _ = decode_capture(png)
        elif key in (None, "official"):
            pin = self.services.library.current_pin()
            if pin is None:
                raise ServiceError("game_not_found")
            raw = self.asset(["default", pin.id, alt + ".png"]).body
        else:
            raw = self.services.photographs.image(alt, key)
        folder = Path(self.services.settings.get("output_dir")).expanduser().resolve()
        devmode.check_write(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^a-zA-Z0-9_-]", "_", alt)[:100] + "-" + (key or "official")
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
            parts: The path after ``photography/``, split at slashes: ``[default, <install id>, <alt>.png]``
                for the official Collection portrait read from the game files, or ``[<alt>, <photo id>.png]``
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
            alt = parts[2][:-4]
            descriptor = self._target(alt)
            paths = s.library.official_bundles(core_of(descriptor["bundle"]), pin)
            paths = [path for path in paths if path.name.removesuffix(".backup") == descriptor["bundle"]]
            return AssetResponse(s.game.collection_portrait_from(descriptor["name"], paths), "image/png", False)
        if len(parts) == 2 and parts[1].endswith(".png"):
            alt, key = parts[0], parts[1][:-4]
            self._alt(alt)
            return AssetResponse(s.photographs.image(alt, key), "image/png", False)
        raise KeyError("photography")


def register(rpc, services: Services) -> None:
    """Register the ``photography.*`` RPC methods.

    Args:
        rpc: The ``RpcRegistry`` to register on.
        services: The Services whose ``photography`` handles the calls.
    """

    @rpc.method("photography.list")
    def listing(alt: str):
        return services.photography.list(alt)

    @rpc.method("photography.save")
    def save(alt: str, png: str, mode: str, face: str = "", action: str = "", source: str = ""):
        return services.photography.save(alt, png, mode, face, action, source)

    @rpc.method("photography.select")
    def select(alt: str, id: str | None):
        return services.photography.select(alt, id)

    @rpc.method("photography.delete")
    def delete(alt: str, id: str, confirm: bool = False):
        return services.photography.delete(alt, id, confirm=confirm)

    @rpc.method("photography.removalInfo")
    def removal_info(alt: str, id: str):
        return services.photography.removal_info(alt, id)

    @rpc.method("photography.apply")
    def apply(alt: str, id: str | None, png: str | None = None):
        return services.photography.apply(alt, id, png=png)

    @rpc.method("photography.restore")
    def restore(alt: str, png: str | None = None):
        return services.photography.restore(alt, png)

    @rpc.method("photography.export")
    def export(alt: str, id: str | None, png: str | None = None):
        return services.photography.export(alt, id, png)
