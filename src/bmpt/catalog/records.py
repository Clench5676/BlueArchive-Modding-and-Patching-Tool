"""JSON-ready student list records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def alt_resource_core(alt: Any, resource_type: str) -> str | None:
    """Find the installed core for a resource type on an alt."""
    return getattr(alt, "memorial_lobby" if resource_type == "memorialLobby" else resource_type, None)


@dataclass
class Alt:
    """A file-id alt and the assets available in the current install."""

    key: str
    file_id: str
    student: str
    names: dict[str, dict[str, str]]
    playable: bool
    order: int
    portrait: str | None
    memorial_lobby: str | None
    background: str | None
    official_thumbnail: str | None
    date: str | None
    page_id: str | None = None  # the student page on Kivo (kivo.wiki), for memorial lobby lines
    released_on_server: bool = False  # playable on the selected server, not only on some server
    # the 3D chibi's dev name (character-<dev>-_mxload-* bundles), or that of a chibi in the alt's own bundles
    # (assets-_mx-npcs|characters-<dev>-*: a story cutscene's or a UI scene's character), its slot either way
    chibi: str | None = None
    # a chibi with no slot, for an alt with no chibi of its own: preview and costume source only, the game has no
    # slot of it to switch. The dev name of a field-mode player (Field_Player_<dev>) or of a field NPC (Field_NPC_<id>)
    field_chibi: str | None = None
    schale_id: int | None = None  # the game's character id (SchaleDB's student id), for the Global server's lines
    # names the student list search matches but the UI never shows (the CN server's zh-CN wording)
    aliases: list[str] = field(default_factory=list)
    spine_category: str | None = None  # a special slot's original bundle category

    def to_json(self) -> dict:
        """Return an independent JSON-ready record for RPC responses."""
        return asdict(self)


@dataclass
class Student:
    """A student, named by their base alt, with installed portrait alts."""

    key: str
    names: dict[str, str]
    school: str | None
    alts: list[Alt]
    aliases: list[str] = field(default_factory=list)  # base names the search matches but the UI never shows

    def to_json(self) -> dict:
        """Return an independent JSON-ready record for RPC responses."""
        return asdict(self)


@dataclass(frozen=True)
class CoreMap:
    """Map one install's portrait and memorial lobby cores to alt keys.

    Catalog.core_map applies the student list's rules to that install's core names, so a
    core gets the same key in every install.
    """

    keys: dict[str, str]  # lower-case core -> alt key
    portrait: dict[str, str]  # alt key -> its portrait core in the install, as named there
    memorial_lobby: dict[str, str]  # alt key -> its primary memorial lobby core in the install, as named there
    names: dict[str, dict[str, dict[str, str]]]  # alt key -> the shipped names, for the keys the data has

    chibi: dict[str, str] = field(default_factory=dict)  # alt key -> installed 3D chibi core
    chibi_cores: dict[str, tuple[str, ...]] = field(default_factory=dict)  # alt key -> prefab first, own dependencies

    def slot(self, core: str) -> str:
        """Return a physical core's choice key, sharing chibi dependencies with their prefab.

        Args:
            core: Installed bundle core, matched without case sensitivity.
        """
        core = core.lower()
        key = self.key(core)
        return self.chibi[key] if core in self.chibi_cores.get(key, ()) else core

    def slot_cores(self, core: str) -> tuple[str, ...]:
        """Return the physical cores of one choice, with the chibi prefab first.

        Args:
            core: Prefab, dependency, or non-chibi core identifying the choice.
        """
        core = core.lower()
        key = self.key(core)
        return self.chibi_cores[key] if core in self.chibi_cores.get(key, ()) else (core,)

    def key(self, core: str) -> str | None:
        """Return a core's alt key, or None when it is absent from the map.

        Args:
            core: Installed bundle core, matched without case sensitivity.
        """
        return self.keys.get(core.lower())

    def core(self, key: str, resource_type: str) -> str | None:
        """Return an alt's installed core for a requested asset category.

        Args:
            key: alt key.
            resource_type: One of portrait, memorialLobby, or chibi.

        Returns:
            The core, or None when the alt or category is unavailable.
        """
        return {"portrait": self.portrait, "memorialLobby": self.memorial_lobby, "chibi": self.chibi}.get(
            resource_type, {}
        ).get(key.lower())
