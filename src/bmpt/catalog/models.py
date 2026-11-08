"""JSON-ready roster records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Variant:
    """A file-id variant and the assets available in the current install."""

    key: str
    file_id: str
    family: str
    names: dict[str, dict[str, str]]
    playable: bool
    order: int
    spr: str | None
    lobby: str | None
    bg: str | None
    portrait: str | None
    date: str | None
    page_id: str | None = None  # the student page on Kivo (kivo.wiki), for lobby lines
    recruitable: bool = False  # playable on the selected server, not only on some server
    model: str | None = None  # the 3D chibi's dev name (character-<dev>-_mxload-* bundles)
    # a model with no slot, for a variant with no character model: preview and costume source only, the game has no
    # slot of it to switch. The dev name of a field-mode player (Field_Player_<dev>), of a field NPC (Field_NPC_<id>)
    # or of a model in the variant's own bundles (a story cutscene's or a UI scene's character)
    field_model: str | None = None
    schale_id: int | None = None  # the game's character id (SchaleDB's student id), for the Global server's lines
    # names the roster search matches but the UI never shows (the CN server's zh-CN wording)
    aliases: list[str] = field(default_factory=list)
    spine_category: str | None = None  # a special slot's original bundle category

    def to_json(self) -> dict:
        """Return an independent JSON-ready record for RPC responses."""
        return asdict(self)


@dataclass
class Family:
    """A student, named by their base variant, with installed portrait variants."""

    key: str
    names: dict[str, str]
    school: str | None
    variants: list[Variant]
    aliases: list[str] = field(default_factory=list)  # base names the search matches but the UI never shows

    def to_json(self) -> dict:
        """Return an independent JSON-ready record for RPC responses."""
        return asdict(self)


@dataclass(frozen=True)
class CoreMap:
    """How one install's portrait and memorial lobby cores map to variant keys (Catalog.core_map): the same rules as
    the roster's, run on that install's core names, so a core gets the same key in every install."""

    keys: dict[str, str]  # lower-case core -> variant key
    spr: dict[str, str]  # variant key -> its portrait core in the install, as named there
    lobby: dict[str, str]  # variant key -> its primary memorial lobby core in the install, as named there
    names: dict[str, dict[str, dict[str, str]]]  # variant key -> the shipped names, for the keys the data has

    model: dict[str, str] = field(default_factory=dict)  # variant key -> installed 3D model core
    model_cores: dict[str, tuple[str, ...]] = field(default_factory=dict)  # variant key -> prefab first, own dependencies

    def slot(self, core: str) -> str:
        """The choice key of a physical core; a model's dependencies share its prefab's choice."""
        core = core.lower()
        key = self.key(core)
        return self.model[key] if core in self.model_cores.get(key, ()) else core

    def slot_cores(self, core: str) -> tuple[str, ...]:
        """Physical cores of one choice, with the model prefab first."""
        core = core.lower()
        key = self.key(core)
        return self.model_cores[key] if core in self.model_cores.get(key, ()) else (core,)

    def key(self, core: str) -> str | None:
        """The variant key of a portrait or lobby core, ignoring letter case; None for a core of neither kind."""
        return self.keys.get(core.lower())

    def core(self, key: str, kind: str) -> str | None:
        """A variant's portrait core ("spr") or primary lobby core ("lobby") in the install."""
        return {"spr": self.spr, "lobby": self.lobby, "model": self.model}.get(kind, {}).get(key.lower())
