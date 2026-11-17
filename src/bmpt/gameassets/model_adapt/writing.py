"""Writing the costume into the slot's bundles: new objects, the sanity checks and the mouth."""

from __future__ import annotations

import copy
import dataclasses
import random
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping

import numpy as np

from ..model import (
    BundleGraph,
    _key,
)

if TYPE_CHECKING:
    pass
from .parts import Part
from .skeleton import BINDPOSE_LIMIT, VERTEX_LIMIT, WEIGHT_TOLERANCE, Skeleton, refuse


def _type_index(sf: Any, source: Any) -> int | None:
    """The index of ``source``'s type among ``sf``'s types, None when ``sf`` has none of it."""
    src_type = source.serialized_type
    return next(
        (
            i
            for i, typ in enumerate(sf.types)
            if typ.class_id == source.class_id
            and typ.old_type_hash == src_type.old_type_hash
            and typ.script_type_index == src_type.script_type_index
        ),
        None,
    )


class _Writer:
    """Objects added to the slot's bundles, opened for writing."""

    def __init__(self, files: Mapping[str, Any], seed: str):
        """Prepare deterministic new object IDs for writable slot serialized files.

        Args:
            files: Lower-case CAB names mapped to (engine Bundle, SerializedFile) pairs.
            seed: Build identity whose CRC32 seeds collision-checked random path IDs.
        """
        self.files = files  # lower-case CAB -> (engine Bundle, SerializedFile)
        self.random = random.Random(zlib.crc32(seed.encode()))

    def file(self, cab: str) -> Any:
        """Return the writable SerializedFile registered under an exact lower-case CAB name."""
        return self.files[cab][1]

    def fresh(self, sf: Any) -> int:
        """Choose a nonzero signed 64-bit path ID absent from the destination serialized file."""
        while True:
            pid = self.random.randrange(-(1 << 63), 1 << 63)
            if pid and pid not in sf.objects:
                return pid

    def add(self, sf: Any, type_name: str, tree: dict) -> int:
        """Add an object using an existing destination type and return its new path ID.

        Args:
            sf: Writable destination serialized file whose objects remain sorted by path ID.
            type_name: Unity type name requiring an existing object as the serialization template.
            tree: New object's typetree to serialize immediately.

        Raises:
            ServiceError: The slot file contains no object of the requested type.
        """
        template = next((o for o in sf.objects.values() if o.type.name == type_name), None)
        if template is None:
            raise refuse("no object type in the slot", type=type_name)
        new = object.__new__(type(template))
        new.__dict__.update(template.__dict__)
        new.path_id = self.fresh(sf)
        new.data = b""
        new.save_typetree(tree)
        sf.objects[new.path_id] = new
        ordered = sorted(sf.objects.items())
        sf.objects.clear()
        sf.objects.update(ordered)
        return new.path_id

    @staticmethod
    def addable(sf: Any, source: Any) -> str | None:
        """Check whether a source object's serialized type can be added to a destination file.

        Args:
            sf: Destination serialized file whose build/version/type-tree settings must match.
            source: Source reader supplying a serialized type and any type dependencies.

        Returns:
            Refusal reason, or None when add_like can use the type.
        """
        src_type, src_file = source.serialized_type, source.assets_file
        if (
            src_type is None
            or sf.header.version != src_file.header.version
            or sf.unity_version != src_file.unity_version
            or sf._enable_type_tree != src_file._enable_type_tree
            or sf.header.version < 16
        ):
            return "object type of another build"
        if _type_index(sf, source) is None and src_type.type_dependencies:
            return "object type with dependencies"
        return None

    def add_like(self, sf: Any, source: Any, tree: dict) -> int:
        """Add an object of the source's type, copying an absent type from the same game build.

        This permits carrying shaders whose type is absent from the destination file.

        Args:
            sf: Writable destination serialized file.
            source: Source object reader providing class/type metadata compatible with the destination build.
            tree: New object's typetree to serialize under a fresh path ID.

        Returns:
            The new object's path ID.

        Raises:
            ServiceError: The builds differ or the absent type has unsupported dependencies.
        """
        why = self.addable(sf, source)
        if why:
            raise refuse(why, type=source.type.name)
        src_type = source.serialized_type
        index = _type_index(sf, source)
        if index is None:
            sf.types.append(copy.copy(src_type))
            index = len(sf.types) - 1
        template = next(iter(sf.objects.values()))
        new = object.__new__(type(template))
        new.__dict__.update(template.__dict__)
        new.type_id, new.serialized_type, new.class_id, new.type = index, sf.types[index], source.class_id, source.type
        new.path_id = self.fresh(sf)
        new.data = b""
        new.save_typetree(tree)
        sf.objects[new.path_id] = new
        ordered = sorted(sf.objects.items())
        sf.objects.clear()
        sf.objects.update(ordered)
        return new.path_id

    def builtin(self, sf: Any, entry: Any) -> int:
        """Register a player resource file, such as Library/unity default resources, by external path.

        Args:
            sf: Destination serialized file whose external table is extended only when needed.
            entry: Source external entry copied verbatim when its case-insensitive path is absent.

        Returns:
            The one-based external file ID.
        """
        for i, e in enumerate(sf.externals):
            if e.path.lower() == entry.path.lower():
                return i + 1
        sf.externals.append(copy.copy(entry))
        return len(sf.externals)

    def external(self, sf: Any, cab: str) -> int:
        """Find or register a CAB reference in a destination serialized file.

        Args:
            sf: Writable serialized file whose first external entry supplies new-entry metadata.
            cab: Lower-case CAB name to register with archive path spelling.

        Returns:
            Zero for the file itself, otherwise a one-based external file ID.
        """
        if cab == sf.name.lower():
            return 0
        for i, e in enumerate(sf.externals):
            if e.path.replace("\\", "/").rsplit("/", 1)[-1].lower() == cab:
                return i + 1
        real = "CAB-" + cab[4:] if cab.startswith("cab-") else cab
        entry = object.__new__(type(sf.externals[0]))
        for slot in ("temp_empty", "guid", "type"):
            setattr(entry, slot, getattr(sf.externals[0], slot, None))
        entry.path = f"archive:/{real}/{real}"
        sf.externals.append(entry)
        return len(sf.externals)


def _matrix(m: np.ndarray) -> dict[str, float]:
    return {f"e{r}{c}": float(m[r, c]) for c in range(4) for r in range(4)}


def _vector(v: Iterable[float]) -> dict[str, float]:
    return dict(zip("xyz", (float(x) for x in v)))


@dataclass
class Written:
    """Retain one written costume mesh's arrays and source constraints for _sanity validation."""

    name: str
    vertices: np.ndarray  # (n, 3), mesh space, as written
    linear: np.ndarray | None  # (n, 3, 3), what turned the normals and tangents
    joints: np.ndarray  # (n, 4), indices into the mesh's bone list
    weights: np.ndarray  # (n, 4)
    bindposes: np.ndarray  # (bones, 4, 4)
    bones: list[str]  # the bones' names, as bindposes
    submeshes: int
    source_totals: np.ndarray | None = (
        None  # per vertex, the source's weight sum (no weight is allowed where it had none)
    )
    source_materials: list[int] = field(default_factory=list)  # the source renderer's materials, 0 for none
    streamed: bool = False  # the written tree still names stream data (its vertices are not inlined)


@dataclass
class Rendered:
    """Record a written renderer's mesh index into Written records, bones, materials, and bounds."""

    part: int
    bones: int
    materials: list[int]  # path ids, 0 for none
    bounds: dict  # m_AABB


def _sanity(meshes: list[Written], renderers: list[Rendered]) -> None:
    """The last check before the bundles are returned.

    What this run wrote must be numbers a player can load. A failure refuses the pair (``source_unsupported``) rather
    than saving a mesh that draws nothing or flies apart. Over arrays already in memory; nothing is read again.
    """
    for mesh in meshes:
        if mesh.streamed:
            raise refuse("streamed mesh", part=mesh.name)
        for what, array in (("bindposes", mesh.bindposes), ("vertices", mesh.vertices), ("normals", mesh.linear)):
            if array is not None and not np.isfinite(array).all():
                raise refuse("invalid data", what=what, part=mesh.name)
        if len(mesh.bindposes):
            largest = np.abs(mesh.bindposes).reshape(len(mesh.bindposes), -1).max(axis=1)
            if largest.max() > BINDPOSE_LIMIT:
                raise refuse("bindpose out of range", bone=mesh.bones[int(np.argmax(largest))], part=mesh.name)
        count, used = len(mesh.bindposes), mesh.weights > 0
        if (mesh.joints[used] >= count).any() or (mesh.joints[used] < 0).any():
            raise refuse("skin inconsistent", part=mesh.name, what="bone index past the bindposes")
        totals = mesh.weights.sum(axis=1)
        none = (
            np.zeros(len(totals), bool)
            if mesh.source_totals is None
            else (totals <= 1e-9) & (mesh.source_totals <= 1e-9)
        )
        if not (none | (np.abs(totals - 1.0) <= WEIGHT_TOLERANCE)).all():
            raise refuse("skin inconsistent", part=mesh.name, what="weights do not add up to 1")
    for renderer in renderers:
        mesh = meshes[renderer.part]
        if renderer.bones != len(mesh.bindposes):
            raise refuse(
                "skin inconsistent",
                part=mesh.name,
                what=f"{renderer.bones} renderer bones for {len(mesh.bindposes)} bindposes",
            )
        centre = np.array([renderer.bounds["m_Center"][axis] for axis in "xyz"], float)
        extent = np.array([renderer.bounds["m_Extent"][axis] for axis in "xyz"], float)
        if not (np.isfinite(centre).all() and np.isfinite(extent).all()):
            raise refuse("invalid data", what="bounds", part=mesh.name)
        if float(np.max(np.abs(centre) + np.abs(extent))) > VERTEX_LIMIT:
            raise refuse("vertices out of range", part=mesh.name)
        if len(renderer.materials) > mesh.submeshes:
            raise refuse(
                "materials inconsistent",
                part=mesh.name,
                what=f"{len(renderer.materials)} materials for {mesh.submeshes} submeshes",
            )
        if len(renderer.materials) != len(mesh.source_materials):  # a null kept in place, never dropped
            raise refuse(
                "materials inconsistent",
                part=mesh.name,
                what=f"{len(renderer.materials)} materials for the source's {len(mesh.source_materials)}",
            )


@dataclass
class Mouth:
    """Locate the source mouth's costume part, material index, and default tile.

    CharacterMouthEventReceiver or CharacterAnimationEventReceiver supplies the locator.
    The part index follows source costume order, primary first; the material index selects
    that part's eye-mouth material.
    """

    part: int
    index: int
    uv: dict


MOUTH_FIELDS = ("MouthRenderer", "MouthMaterialIndex", "MouthDefaultUV")


def mouth_of(skeleton: Skeleton, costume: list[Part]) -> Mouth | None:
    """Find a mouth receiver pointing to an eye-mouth material on a selected costume part.

    Args:
        skeleton: Source hierarchy whose mouth/animation-event behaviours supply receiver fields.
        costume: Selected source costume parts, primary first, defining the returned part index.

    Returns:
        Mouth locator, or None for absent/invalid receivers, such as index -1 or an excluded cut-in body.
    """
    g = skeleton.graph
    parts = {_key(p.renderer): k for k, p in enumerate(costume)}
    for behaviour in skeleton.behaviours:
        tree = g.tree(behaviour)
        if not all(name in tree for name in MOUTH_FIELDS):
            continue
        renderer = g.deref(behaviour, tree["MouthRenderer"])
        k = parts.get(_key(renderer)) if renderer is not None else None
        index = int(tree["MouthMaterialIndex"])
        if k is None or not 0 <= index < len(costume[k].kinds) or costume[k].kinds[index] != "eyemouth":
            continue
        return Mouth(k, index, copy.deepcopy(tree["MouthDefaultUV"]))
    return None


MOUTH_ATLAS = "_MouthTileTex"  # the eye-mouth material's mouth atlas
MOUTH_GRID = ("_MouthTileCols", "_MouthTileRows")  # its cells across and up


@dataclass
class MouthAtlas:
    """Describe an eye-mouth material's texture, scale/offset, and mouth-cell grid.

    Retain _MouthTileTex and MOUTH_GRID as the material stores them. A clip's SetMouthTile events name grid cells
    (row * 100 + column, rows from the bottom), so the same number draws another mouth on another atlas: most
    students share one atlas, others draw their own (Hina's 4 x 4 one), laid out differently.
    """

    material: Any
    env: dict
    grid: dict[str, float]
    texture: tuple[str, int] | None

    def same(self, other: "MouthAtlas") -> bool:
        """Check whether a mouth-cell number uses the same texture, scale, and grid as another atlas."""
        return (
            self.texture == other.texture
            and self.env.get("m_Scale") == other.env.get("m_Scale")
            and self.grid == other.grid
        )


def mouth_atlas(graph: BundleGraph, material: Any) -> MouthAtlas | None:
    """Read a material's mouth texture environment and grid.

    Args:
        graph: Dependency graph resolving the texture and material typetree.
        material: Eye-mouth Material reader; None or an absent mouth texture returns None.
    """
    if material is None:
        return None
    props = graph.tree(material)["m_SavedProperties"]
    env = next((value for name, value in props.get("m_TexEnvs", []) if name == MOUTH_ATLAS), None)
    if env is None or not env["m_Texture"]["m_PathID"]:
        return None
    texture = graph.deref(material, env["m_Texture"])
    grid = {name: float(value) for name, value in props.get("m_Floats", []) if name in MOUTH_GRID}
    return MouthAtlas(material, copy.deepcopy(env), grid, _key(texture) if texture is not None else None)


def retiled_mouths(
    graph: BundleGraph, t_skeleton: Skeleton, t_costume: list[Part], s_costume: list[Part], mouth: Mouth | None
) -> tuple[MouthAtlas | None, set[tuple[str, int]], Mouth | None]:
    """Identify source eye-mouth materials needing the slot's atlas and default tile.

    Slot clips retain SetMouthTile events naming the slot's atlas cells. Materials drawing
    a different atlas take its texture, scale, offset, and grid; receivers retain the slot's default tile.

    Args:
        graph: Shared dependency graph resolving slot and source materials/textures.
        t_skeleton: Slot hierarchy whose mouth receiver defines the retained atlas and default tile.
        t_costume: Slot costume part order used by its mouth locator.
        s_costume: Incoming parts whose eye-mouth materials are compared against the slot atlas.
        mouth: Incoming mouth locator; None requires no retile operation.

    Returns:
        Slot atlas, graph keys of materials requiring replacement, and the source mouth
        with the receiver's default tile. Without either mouth/atlas, return None, an empty set, and mouth.
    """
    t_mouth = mouth_of(t_skeleton, t_costume)
    slot = mouth_atlas(graph, t_costume[t_mouth.part].materials[t_mouth.index]) if t_mouth is not None else None
    if slot is None or mouth is None:
        return None, set(), mouth
    retiled = set()
    for part in s_costume:
        for material, kind in zip(part.materials, part.kinds):
            own = mouth_atlas(graph, material) if kind == "eyemouth" else None
            if own is not None and not own.same(slot):
                retiled.add(_key(material))
    if _key(s_costume[mouth.part].materials[mouth.index]) in retiled:
        mouth = dataclasses.replace(mouth, uv=copy.deepcopy(t_mouth.uv))
    return slot, retiled, mouth


# what a build cannot show in the preview and the game may draw differently (the switch's cautions)
EX_PARTS = "ex_parts"  # the slot's EX-only parts (a cut-in body, alternative faces) draw nothing
CARRIED_SHADER = "carried_shader"  # a shader carried with the costume: the game holds two of its name
