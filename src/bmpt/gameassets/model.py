"""In-memory export of a student's 3D chibi (prefab, meshes, skins, textures, clips) to one GLB.

The prefabs are in the bundle family ``character-<dev>-_mxload-*``; their meshes, materials, textures and clips sit
in other bundles and are reached through external CAB references. :class:`BundleGraph` loads only the bundles the
used objects point into, by a CAB -> bundle index. :func:`export_chibi` walks one prefab and writes the skinned
meshes, the skeleton, the textures as PNG (with the game's samplers and colour spaces), the game's material constants
and render states (material extras), the halo follower and the mouth receiver (root extras), the halo's particle
systems (root extras, :meth:`_Exporter.halo_fx`) and the clips, decoded from Unity's streamed, dense and constant
curves, with the renderers they switch on and off (animation extras).

Unity is left-handed (+Z forward), glTF right-handed: positions, rotations and bind matrices are mirrored on X.
Nothing is written to disk.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import struct
import zlib
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import UnityPy.helpers.MeshHelper as MeshHelper
from UnityPy.helpers.MeshHelper import MeshHandler
from UnityPy.helpers.PackedBitVector import reshape, unpack_floats, unpack_ints

from ..texture_guard import texture_image
from . import particles
from .discovery import cab_names, own_bundle
from .errors import AssetNotFound
from .unity import open_bundle

logger = logging.getLogger(__name__)

# The clips put in the GLB (by name suffix): the formation idle (weapon in hand) is the default loop.
STORY_CLIPS = 12  # a story model's own cutscene clips exported at most
STORY_ROOTS = {"bone_root", "bone_root/Bip001"}  # the nodes a cutscene places its character by
DEFAULT_CLIPS = (
    "Formation_Idle",
    "Formation_Pickup",
    "Cafe_Idle",
    "Cafe_Reaction",
    "Cafe_Walk",
    "Victory_Start",
    "Victory_End",
    "Vital_Retreat",
    "Vital_Death",
)
# other names a slot's clip goes by: misspelt ones, and the first form's of a student with two battle forms
# (CH0187, CH0368, Hoshino's swimsuit: Vital_Death01 and Vital_Death02)
CLIP_ALIASES = {
    "Formation_Pickup": ("Fomation_Pickup", "Formation Pickup"),
    "Vital_Death": ("Vital_Death01",),
    "Vital_Retreat": ("Vital_Retreat01",),
}
# idles used when a prefab has neither the formation nor the cafe idle (NPC and battle-only prefabs)
FALLBACK_IDLES = ("Normal_Idle", "Stand_Idle", "Idle")
PREFAB_KINDS = {
    "echelon": "Echelon_{}",
    "battle": "{}",
    "cafe": "Cafe_{}",
    "strategy": "Strategy_{}",
    # field-mode players (event and story variants with no battle model), Animancer-driven
    "field": "Field_Player_{}",
    "field_nonweapon": "Field_Player_{}_NonWeapon",
}
# field-mode NPCs, named by the character's id and a description (Field_NPC_NP0060_Justice_Student, one per weapon for
# NP0143: _AR_Weapon, _SG_Weapon, _SR_Weapon), in the field character bundles beside the field players
NPC_PREFAB = re.compile(r"field_npc_((?:ch|np)\d{4})(?:_\w+)?", re.IGNORECASE)
# the viewer's shading model for a shader, by name prefix (variants such as -Inline, -Transparent or -NoOutline share
# their base shader's lighting); the first match wins
SHADER_KINDS = (
    ("MX/C-General/Layer4", "body"),
    ("MX/C-Face", "face"),
    ("MX/C-Hair", "hair"),
    ("MX/C-EyesMouth", "eyemouth"),
    ("MX/C-Eyebrow", "eyebrow"),
    ("MX/C-Halo", "halo"),
    ("MX/C-Weapon", "weapon"),
    ("ProjectMX/Weapon", "weapon"),
    ("MX/C-Simple-Transparent", "transparent"),
    ("MX/C-Transparent", "transparent"),
    ("MX/C-Simple", "simple"),
    ("MX/C-General", "general"),
    ("MX/E-Water", "water"),
    ("MX/Unlit", "unlit"),
    ("Unlit/", "unlit"),
    ("DSFX/FX_SHADER_Matcap", "matcap"),
    ("FX/", "unlit"),
    ("DSFX/", "unlit"),
)


def shader_kind(shader: str | None) -> str:
    """Return a shader name's shading model, defaulting to MX/C-General/Single's "general"."""
    for prefix, kind in SHADER_KINDS:
        if shader and shader.startswith(prefix):
            return kind
    return "general"


# the objects of an EX skill's cut-in, by hierarchy path
CUT_IN = re.compile(r"cut_?in", re.IGNORECASE)


def ex_only_renderers(renderers: Iterable[tuple[int, str, list[tuple[str, int]]]]) -> set[int]:
    """Identify enabled prefab renderers that the game shows only during an EX skill.

    - Cut-in bodies: a second body with a face for the skill's cut-in (``CH0123_CutIn_Body``), beside the body that is
      not one. Cut-in props (a chair) stay: the clips scale them away like any prop.
    - Alternative expression faces: of the face renderers (a face part at least half the largest one), the ones
      without eyes, beside one with them. The prefab enables them all, and the gag face lies in front of the eyes, so
      drawn together it hides them; the skill's timeline switches the faces (Kasumi's turns her face with eyes off),
      and where a clip does it, every clip turns the face without eyes off (Miyu, Kaede, Tsukuyo).

    Args:
        renderers: Enabled renderers as (node, hierarchy path, submesh shading-kind/index-count pairs).
    """
    renderers = list(renderers)
    with_face = {node for node, _, parts in renderers if any(kind == "face" for kind, _ in parts)}
    out = {node for node, path, _ in renderers if node in with_face and CUT_IN.search(path)}
    if out == with_face:
        out = set()
    faces = {
        node: (sum(count for kind, count in parts if kind == "face"), any(kind == "eyemouth" for kind, _ in parts))
        for node, _, parts in renderers
        if node in with_face and node not in out
    }
    largest = max((size for size, _ in faces.values()), default=0)
    main = {node: eyes for node, (size, eyes) in faces.items() if largest and size >= 0.5 * largest}
    if any(main.values()):
        out |= {node for node, eyes in main.items() if not eyes}
    return out


_ARRAY = 34962
_INDICES = 34963


def safe_unpack_floats(packed: Any, start: int = 0, count: int | None = None, shape: tuple | None = None) -> list:
    """Unpack packed floats while treating zero-bit values as the constant m_Start.

    The engine unpacker divides by (1 << 0) - 1 for a zero-bit stream.

    Args:
        packed: PackedFloatVector with item count, bit width, range, and starting value.
        start: First packed item to decode.
        count: Number of items to decode; None reads from start to the declared end.
        shape: Optional dimensions passed to the unpacker's reshape operation.
    """
    if not packed.m_BitSize:
        n = (packed.m_NumItems - start) if count is None else count
        return reshape([float(packed.m_Start)] * n, shape)
    return unpack_floats(packed, start, count, shape)


# MeshHandler looks the function up in its own module; only the m_BitSize == 0 case behaves differently
MeshHelper.unpack_floats = safe_unpack_floats


def compressed_skin(
    weights: list[int], bones: list[int], vertex_count: int
) -> tuple[list[list[float]], list[list[int]]]:
    """Decode compressed per-vertex weights and bone indices with integer weight sums.

    Weights are 5-bit (a vertex's weights add up to 31). After three weights that fall short, the fourth is implied
    (31 - sum) and only its bone index is stored. UnityPy sums the weights as floats, where 1/31 + 25/31 + 5/31 < 1,
    reads a phantom fourth weight and shifts every later vertex.

    Args:
        weights: Unpacked 5-bit weights, omitting an implied fourth influence after three incomplete weights.
        bones: Unpacked bone indices, including the implied fourth influence's index.
        vertex_count: Number of four-influence rows to produce, padding absent data with zeros.
    """
    w_rows = [[0, 0, 0, 0] for _ in range(vertex_count)]
    j_rows = [[0, 0, 0, 0] for _ in range(vertex_count)]
    bone = lambda i: bones[i] if i < len(bones) else 0  # noqa: E731
    v = j = total = bi = 0
    for w in weights:
        if v >= vertex_count:
            break
        w_rows[v][j], j_rows[v][j] = w, bone(bi)
        bi += 1
        j += 1
        total += w
        if total >= 31:
            v, j, total = v + 1, 0, 0
        elif j == 3:
            w_rows[v][3], j_rows[v][3] = 31 - total, bone(bi)
            bi += 1
            v, j, total = v + 1, 0, 0
    return [[x / 31 for x in row] for row in w_rows], j_rows


# VertexFormat (Unity 2019+): UNorm8, SNorm8, UNorm16, SNorm16
_NORM = {2: 255.0, 3: 127.0, 4: 65535.0, 5: 32767.0}
_CHANNEL_ATTRS = {
    0: "m_Vertices",
    1: "m_Normals",
    2: "m_Tangents",
    3: "m_Colors",
    4: "m_UV0",
    5: "m_UV1",
    6: "m_UV2",
    7: "m_UV3",
    12: "m_BoneWeights",
}


def fix_vertex_formats(mesh: Any, handler: Any) -> None:
    """Scale normalized integer vertex channels to floats in a processed mesh handler.

    Raw UNorm8 colors span 0..255; outline width uses alpha and requires normalized values.

    Args:
        mesh: Unity Mesh supplying the vertex channel formats.
        handler: Processed MeshHandler whose decoded channel attributes are corrected in place.
    """
    data = mesh.m_VertexData
    if data is None or not data.m_Channels or tuple(getattr(handler, "version", (2019,)))[0] < 2019:
        return
    for channel, info in enumerate(data.m_Channels):
        scale, attr = _NORM.get(info.format), _CHANNEL_ATTRS.get(channel)
        if not info.dimension or scale is None or attr is None:
            continue
        values = getattr(handler, attr, None)
        if values:
            setattr(handler, attr, [tuple(max(-1.0, x / scale) for x in v) for v in values])


def pad_skin(joints: list | None, weights: list | None, count: int) -> tuple[list | None, list | None]:
    """Pad joint rows to four influences and normalize vertex weights.

    Rigid props have one BlendIndices component and no BlendWeight channel: that is
    weight 1 on the one bone.

    Args:
        joints: Per-vertex bone-index rows; None or an empty list means no skin.
        weights: Per-vertex influence weights; None or empty assigns weight one to the first joint.
        count: Vertex count used to create rigid-prop weight rows when weights are absent.

    Returns:
        Four-component joint/weight rows, or (None, None) for an unskinned mesh.
    """
    if not joints:
        return None, None
    joints = [tuple(int(x) for x in j) + (0,) * (4 - len(j)) for j in joints]
    if not weights:
        weights = [(1.0, 0.0, 0.0, 0.0)] * count
    out = []
    for w in weights:
        w = tuple(w) + (0.0,) * (4 - len(w))
        total = sum(w) or 1.0
        out.append(tuple(x / total for x in w))
    return joints, out


@dataclass
class MeshData:
    """Hold decoded vertex attributes and baseVertex-adjusted triangles per submesh."""

    vertices: list
    normals: list | None
    uv0: list | None
    uv1: list | None
    colors: list | None
    tangents: list | None
    joints: list | None
    weights: list | None
    triangles: list[list[tuple[int, int, int]]]


def read_mesh(mesh: Any) -> MeshData:
    """Decode a Unity Mesh with normalized channels, corrected compressed skin, and baseVertex-adjusted triangles."""
    cm = mesh.m_CompressedMesh
    packed_skin = bool(cm and cm.m_Weights.m_NumItems)
    if packed_skin:
        # UnityPy's own weight decoding can run past the bone index list (StopIteration): skip it, decode below
        items, cm.m_Weights.m_NumItems = cm.m_Weights.m_NumItems, 0
    try:
        handler = MeshHandler(mesh)
        handler.process()
    finally:
        if packed_skin:
            cm.m_Weights.m_NumItems = items
    count = handler.m_VertexCount
    fix_vertex_formats(mesh, handler)
    if packed_skin:
        weights, joints = compressed_skin(unpack_ints(cm.m_Weights), unpack_ints(cm.m_BoneIndices), count)
    else:
        weights, joints = handler.m_BoneWeights, handler.m_BoneIndices
    joints, weights = pad_skin(joints, weights, count)
    triangles = []
    for sub, tris in zip(mesh.m_SubMeshes, handler.get_triangles()):
        base = getattr(sub, "baseVertex", 0) or 0
        triangles.append([(a + base, b + base, c + base) for a, b, c in tris])
    return MeshData(
        handler.m_Vertices or [],
        handler.m_Normals,
        handler.m_UV0,
        handler.m_UV1,
        handler.m_Colors,
        handler.m_Tangents,
        joints,
        weights,
        triangles,
    )


# --- math (Unity -> glTF: mirror X) -------------------------------------------------------------------------------


def mpos(v: Iterable[float]) -> tuple[float, float, float]:
    """Mirror a three-component Unity position on X for glTF."""
    x, y, z = v
    return (-x, y, z)


def mquat(q: Iterable[float]) -> tuple[float, float, float, float]:
    """Mirror an (x, y, z, w) Unity quaternion on X for glTF."""
    x, y, z, w = q
    return (x, -y, -z, w)


def mirror_matrix(e: Mapping[str, float]) -> list[float]:
    """Convert an e<row><col> Unity Matrix4x4 typetree to X-mirrored S*M*S in glTF column order."""
    s = (-1, 1, 1, 1)
    return [e[f"e{r}{c}"] * s[r] * s[c] for c in range(4) for r in range(4)]


def quat_norm(q: Iterable[float]) -> tuple[float, ...]:
    """Normalize quaternion components, leaving a zero quaternion unchanged."""
    q = tuple(q)
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return tuple(x / n for x in q)


def qmul(a: tuple, b: tuple) -> tuple[float, float, float, float]:
    """Multiply (x, y, z, w) quaternions in a * b order."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


# Unity's rotation orders (math::RotationOrder), the axis applied first leading; an Euler binding of a clip carries
# its order in customType: 4 (ZXY, Unity's own, Quaternion.Euler) for curves made in Unity, 0 (XYZ) for the ones a
# DCC tool's FBX kept as Euler (NP0186's battle idle)
ROTATION_ORDERS = {0: "XYZ", 1: "XZY", 2: "YZX", 3: "YXZ", 4: "ZXY", 5: "ZYX"}
UNITY_ORDER = 4


def euler_to_quat(e: Iterable[float], order: int = UNITY_ORDER) -> tuple[float, float, float, float]:
    """Convert Unity Euler degrees to an (x, y, z, w) quaternion.

    Unity's ZXY order applies Z, then X, then Y: q = qy * qx * qz.

    Args:
        e: X, Y, and Z Euler angles in degrees.
        order: Unity rotation-order code from ROTATION_ORDERS; unknown codes use ZXY.
    """
    x, y, z = (math.radians(a) / 2 for a in e)
    axes = {
        "X": (math.sin(x), 0, 0, math.cos(x)),
        "Y": (0, math.sin(y), 0, math.cos(y)),
        "Z": (0, 0, math.sin(z), math.cos(z)),
    }
    q: tuple = (0.0, 0.0, 0.0, 1.0)
    for axis in ROTATION_ORDERS.get(order, ROTATION_ORDERS[UNITY_ORDER]):
        q = qmul(axes[axis], q)
    return q


# --- animation clips (generic mecanim clips: streamed + dense + constant curves) -----------------------------------


def binding_dims(binding: Mapping[str, int]) -> int:
    """Count generic-binding curves: three for Transform position/scale/Euler, four for rotation, otherwise one."""
    if binding["typeID"] == 4:
        return 4 if binding["attribute"] == 2 else 3 if binding["attribute"] in (1, 3, 4) else 1
    return 1


def read_streamed(words: list[int]) -> list[tuple[float, list[tuple[int, float, float, float, float]]]]:
    """Read streamed clip frames as (time, curve/cubic-coefficient tuples).

    Each key stores the cubic of the segment that starts at it, value(t) = ((c0*dt + c1)*dt + c2)*dt + c3. The first
    frame is a slope helper at -inf, the last one sits at +inf.

    Args:
        words: Serialized StreamedClip.data as unsigned 32-bit words in little-endian order.
    """
    data = struct.pack(f"<{len(words)}I", *words)
    frames, pos = [], 0
    while pos + 8 <= len(data):
        time, count = struct.unpack_from("<fi", data, pos)
        pos += 8
        keys = [struct.unpack_from("<i4f", data, pos + i * 20) for i in range(count)]
        pos += count * 20
        frames.append((time, keys))
    return frames


@dataclass
class DecodedClip:
    """Hold clip duration and sample times in seconds, with scalar curves by storage index."""

    duration: float
    times: list[float]
    curves: dict[int, list[float]]  # curve index -> one value per time, or a single value (constant)


def decode_clip(tt: Mapping[str, Any]) -> DecodedClip:
    """Sample a clip on its uniform sample-rate grid, indexing streamed, dense, then constant curves.

    Args:
        tt: AnimationClip typetree with muscle timing and curve storage; a missing sample rate uses 30 Hz.

    Returns:
        Duration and times in seconds, with sampled curves and single-value constants.
    """
    muscle = tt["m_MuscleClip"]
    clip = muscle["m_Clip"]["data"]
    start, stop = muscle["m_StartTime"], muscle["m_StopTime"]
    rate = tt.get("m_SampleRate") or 30.0
    times = [start + i / rate for i in range(max(1, int(round((stop - start) * rate)) + 1))]
    streamed, dense, constant = clip["m_StreamedClip"], clip["m_DenseClip"], clip["m_ConstantClip"]
    curves: dict[int, list[float]] = {}
    keys: dict[int, list[tuple]] = {}
    for time, frame in read_streamed(streamed["data"]) if streamed["data"] else []:
        if -1e30 < time < 1e30:
            for curve, c0, c1, c2, c3 in frame:
                keys.setdefault(curve, []).append((time, c0, c1, c2, c3))
    for curve, ks in keys.items():
        values, j = [], 0
        for t in times:
            while j + 1 < len(ks) and ks[j + 1][0] <= t + 1e-6:
                j += 1
            kt, c0, c1, c2, c3 = ks[j]
            dt = t - kt
            values.append(c3 if dt < 0 else ((c0 * dt + c1) * dt + c2) * dt + c3)  # before the first key: hold it
        curves[curve] = values
    offset = streamed["curveCount"]
    count, frames = dense["m_CurveCount"], dense["m_FrameCount"]
    if count and frames:
        samples, drate, begin = dense["m_SampleArray"], dense["m_SampleRate"], dense["m_BeginTime"]
        for i in range(count):
            values = []
            for t in times:
                f = min(max((t - begin) * drate, 0), frames - 1)
                f0 = int(f)
                f1, a = min(f0 + 1, frames - 1), f - f0
                values.append(samples[f0 * count + i] * (1 - a) + samples[f1 * count + i] * a)
            curves[offset + i] = values
    for i, value in enumerate(constant["data"]):
        curves[offset + count + i] = [value]
    return DecodedClip(stop - start, times, curves)


def bind_tracks(
    bindings: list[Mapping[str, int]], curves: Mapping[int, list[float]], nodes: Mapping[int, int]
) -> dict[int, dict[int, list[list[float]]]]:
    """Group Transform curves by node and attribute, retaining one list per component.

    Attributes are 1 position, 2 rotation, 3 scale, and 4 Euler. Other types or paths
    advance the storage index without producing a track.

    Args:
        bindings: Generic bindings in curve-storage order.
        curves: Decoded scalar samples indexed across streamed, dense, and constant storage.
        nodes: Hierarchy-path CRC32 values mapped to destination node indices.
    """
    out: dict[int, dict[int, list[list[float]]]] = {}
    index = 0
    for binding in bindings:
        dims = binding_dims(binding)
        if binding["typeID"] == 4 and binding["attribute"] in (1, 2, 3, 4) and binding["path"] in nodes:
            parts = [curves.get(index + k) for k in range(dims)]
            if all(p is not None for p in parts):
                out.setdefault(nodes[binding["path"]], {})[binding["attribute"]] = parts
        index += dims
    return out


def euler_orders(bindings: list[Mapping[str, int]], nodes: Mapping[int, int]) -> dict[int, int]:
    """Find each Euler-animated node's Unity rotation-order code.

    Args:
        bindings: Generic bindings carrying customType rotation orders from ROTATION_ORDERS.
        nodes: Hierarchy-path CRC32 values mapped to destination node indices.
    """
    return {
        nodes[b["path"]]: b.get("customType", UNITY_ORDER)
        for b in bindings
        if b["typeID"] == 4 and b["attribute"] == 4 and b["path"] in nodes
    }


# switches a clip can animate: a renderer's m_Enabled (Renderer 25, MeshRenderer 23, SkinnedMeshRenderer 137) and a
# GameObject's m_IsActive (1); the attribute of a non-Transform binding is the crc32 of the property name
TOGGLE_TYPES = {25: "renderer", 23: "renderer", 137: "renderer", 1: "active"}
TOGGLE_ATTRIBUTES = {"renderer": zlib.crc32(b"m_Enabled"), "active": zlib.crc32(b"m_IsActive")}


def bind_toggles(
    bindings: list[Mapping[str, int]], curves: Mapping[int, list[float]], nodes: Mapping[int, int]
) -> list[tuple[int, str, list[float]]]:
    """Bind renderer and GameObject switch curves using the same index walk as bind_tracks.

    Args:
        bindings: Generic bindings in curve-storage order; object-reference curves are omitted.
        curves: Decoded scalar samples indexed across streamed, dense, and constant storage.
        nodes: Hierarchy-path CRC32 values mapped to destination node indices.

    Returns:
        (Node, "renderer" or "active", values) tuples for supported switches.
    """
    out, index = [], 0
    for binding in bindings:
        kind = TOGGLE_TYPES.get(binding["typeID"])
        if (
            kind
            and binding["attribute"] == TOGGLE_ATTRIBUTES[kind]
            and binding["path"] in nodes
            and curves.get(index) is not None
            and not binding.get("isPPtrCurve")
        ):
            out.append((nodes[binding["path"]], kind, curves[index]))
        index += binding_dims(binding)
    return out


def toggle_keys(values: list[float], times: list[float], start: float = 0.0) -> list[list[float]]:
    """Reduce a switch curve to change points, treating values at least 0.5 as on.

    Args:
        values: Sampled switch values, or one constant value.
        times: Clip sample times in seconds, aligned with nonconstant values.
        start: Clip start time in seconds, subtracted from output times; constants start at zero.
    """
    keys: list[list[float]] = []
    for i, value in enumerate(values):
        state = 1 if value >= 0.5 else 0
        if not keys or keys[-1][1] != state:
            time = 0.0 if len(values) == 1 else round(times[i] - start, 4)
            keys.append([time, state])
    return keys


def track_values(
    attribute: int, parts: list[list[float]], order: int = UNITY_ORDER
) -> tuple[str, list[tuple[float, ...]]]:
    """Convert component curves to mirrored glTF tracks with continuous normalized rotations.

    Args:
        attribute: Unity Transform attribute: 1 position, 2 quaternion, 3 scale, or 4 Euler degrees.
        parts: One sample list per component; single-value lists are constant across the track.
        order: Unity rotation-order code used for Euler curves.

    Returns:
        glTF channel path and tuples of converted values.
    """
    length = max(len(p) for p in parts)
    rows = [tuple(p[i] if len(p) > 1 else p[0] for p in parts) for i in range(length)]
    if attribute == 1:
        return "translation", [mpos(v) for v in rows]
    if attribute == 3:
        return "scale", rows
    quats = [quat_norm(mquat(v if attribute == 2 else euler_to_quat(v, order))) for v in rows]
    for i in range(1, len(quats)):
        if sum(a * b for a, b in zip(quats[i], quats[i - 1])) < 0:
            quats[i] = tuple(-x for x in quats[i])
    return "rotation", quats


def pick_clips(names: list[str], wanted: Iterable[str] = DEFAULT_CLIPS, dev: str = "") -> list[tuple[int, str]]:
    """Choose one clip per requested slot, preferring the prefab's own clips.

    Prefer the prefab's own (``<dev>_<slot>``)
    before a shared one, a slot's own name before its :data:`CLIP_ALIASES`. When neither the formation nor the cafe
    idle is there, the first fallback idle comes along as slot 'Idle'.

    Args:
        names: Clip asset names whose indices identify the selected readers.
        wanted: Animation slot suffixes to export, tried before each slot's aliases.
        dev: Prefab dev name giving exact <dev>_<slot> matches precedence over shared clips.

    Returns:
        Source-list index and exported slot-name pairs.
    """

    def named(suffix: str) -> int | None:
        hits = [i for i, n in enumerate(names) if n.lower().endswith("_" + suffix.lower())]
        own = [i for i in hits if names[i].lower() == f"{dev}_{suffix}".lower()]
        return (own or hits or [None])[0]

    def best(slot: str) -> int | None:
        return next((i for suffix in (slot, *CLIP_ALIASES.get(slot, ())) if (i := named(suffix)) is not None), None)

    chosen = [(i, slot) for slot in wanted if (i := best(slot)) is not None]
    if not any(slot in ("Formation_Idle", "Cafe_Idle") for _, slot in chosen):
        for suffix in FALLBACK_IDLES:
            if (i := best(suffix)) is not None:
                chosen.append((i, "Idle"))
                break
    return chosen


# --- GLB -----------------------------------------------------------------------------------------------------------

FLT_MAX = 3.4028234663852886e38  # the largest float32


def finite_json(value: Any) -> Any:
    """Copy a JSON tree, replacing infinities with signed FLT_MAX and NaN with zero.

    FLT_MAX remains effectively unbounded for shaders. Strict glTF JSON parsers reject
    nonfinite numbers, which occur in _CameraFadeParams for some NPCs and Saya.

    Args:
        value: Nested dictionaries, lists/tuples, and scalar JSON values; tuples become lists and other leaves are kept.
    """
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return math.copysign(FLT_MAX, value) if math.isinf(value) else 0.0
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    return value


class GlbWriter:
    """Build glTF 2.0 JSON and one binary buffer with 4-byte-aligned buffer views."""

    def __init__(self, generator: str = "BA Modding and Patching Tool"):
        """Start an empty scene, recording generator as the glTF asset's producer label."""
        self.json: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": generator},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "materials": [],
            "textures": [],
            "images": [],
            "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
            "accessors": [],
            "bufferViews": [],
            "buffers": [],
            "skins": [],
            "animations": [],
        }
        self.bin = bytearray()

    def _align(self) -> None:
        self.bin += b"\0" * (-len(self.bin) % 4)

    def view(self, data: bytes, target: int | None = None) -> int:
        """Append aligned bytes and return their buffer-view index.

        Args:
            data: Binary payload to append without altering its contents.
            target: Optional glTF buffer target, 34962 for vertex data or 34963 for indices.
        """
        self._align()
        view = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.bin += data
        self.json["bufferViews"].append(view)
        return len(self.json["bufferViews"]) - 1

    def accessor(
        self, values: list, kind: str, component: int = 5126, target: int | None = None, minmax: bool = False
    ) -> int:
        """Pack numeric values into an accessor and return its index.

        Args:
            values: Scalar items or rows matching the requested kind's component count.
            kind: glTF accessor shape: SCALAR, VEC2, VEC3, VEC4, or MAT4.
            component: glTF component code: 5126 float, 5123 uint16, 5125 uint32, or 5121 uint8.
            target: Optional buffer target forwarded to the new view.
            minmax: Whether to store per-component bounds for a nonempty accessor.
        """
        size = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[kind]
        flat = [x for v in values for x in v] if size > 1 else list(values)
        fmt = {5126: "f", 5123: "H", 5125: "I", 5121: "B"}[component]
        accessor = {
            "bufferView": self.view(struct.pack(f"<{len(flat)}{fmt}", *flat), target),
            "componentType": component,
            "count": len(values),
            "type": kind,
        }
        if minmax and flat:
            columns = [flat[i::size] for i in range(size)]
            accessor["min"] = [min(c) for c in columns]
            accessor["max"] = [max(c) for c in columns]
        self.json["accessors"].append(accessor)
        return len(self.json["accessors"]) - 1

    def sampler(self, sampler: Mapping[str, int]) -> int:
        """Return a glTF filter/wrap sampler's index, adding equal settings only once."""
        samplers = self.json["samplers"]
        if dict(sampler) not in samplers:
            samplers.append(dict(sampler))
        return samplers.index(dict(sampler))

    def image(self, png: bytes, name: str, linear: bool = False, sampler: Mapping[str, int] | None = None) -> int:
        """Embed a PNG image and return its texture index.

        Args:
            png: Complete encoded PNG bytes to place in the binary buffer.
            name: Asset name recorded on both the image and texture.
            linear: Whether masks/direction maps bypass sRGB decoding, recorded in extras because glTF lacks a flag.
            sampler: Filter and wrapping settings; None or empty uses the first sampler (trilinear, repeat).
        """
        self.json["images"].append({"bufferView": self.view(png), "mimeType": "image/png", "name": name})
        texture: dict[str, Any] = {
            "sampler": self.sampler(sampler) if sampler else 0,
            "source": len(self.json["images"]) - 1,
            "name": name,
        }
        if linear:
            texture["extras"] = {"linear": True}
        self.json["textures"].append(texture)
        return len(self.json["textures"]) - 1

    def finish(self) -> bytes:
        """Serialize the scene and aligned binary buffer as GLB bytes with finite JSON numbers."""
        self._align()
        self.json["buffers"] = [{"byteLength": len(self.bin)}]
        doc = finite_json({k: v for k, v in self.json.items() if v != []})
        # allow_nan=False: anything non-finite left over raises here instead of breaking the viewer's JSON.parse
        text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        text += b" " * (-len(text) % 4)
        total = 12 + 8 + len(text) + 8 + len(self.bin)
        return (
            struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<I4s", len(text), b"JSON")
            + text
            + struct.pack("<I4s", len(self.bin), b"BIN\0")
            + bytes(self.bin)
        )


# --- the object graph -----------------------------------------------------------------------------------------------


def _key(obj: Any) -> tuple[str, int]:
    return obj.assets_file.name.lower(), obj.path_id


def _external_cab(obj: Any, file_id: int) -> str:
    if file_id == 0:
        return obj.assets_file.name.lower()
    return obj.assets_file.externals[file_id - 1].path.replace("\\", "/").rsplit("/", 1)[-1].lower()


class BundleGraph:
    """Index loaded objects by CAB and path ID, loading referenced bundles on demand.

    Only bundles that the model's objects reference are read.

    Every bundle stays open (with its process-wide path lock held) until :meth:`close`. Callers serialise exports,
    and every other reader holds one path lock at a time, so holding several cannot deadlock. ``fallback`` is asked
    once for a wider CAB index when a CAB is not in ``index``. Override bundles (mods) take over the CABs they hold.
    """

    def __init__(
        self,
        index: Mapping[str, Any],
        overrides: Iterable[Path] = (),
        *,
        fallback: Callable[[], Mapping[str, Any]] | None = None,
        opener: Callable = open_bundle,
        resolver: Callable[[set[str], Path | None], Mapping[str, str]] | None = None,
    ):
        """Configure dependency lookup and bundle lifetimes for one graph traversal.

        Args:
            index: Lower-case CAB names mapped to bundle paths.
            overrides: Mod bundles whose CABs replace entries in the supplied index.
            fallback: Optional one-shot callback supplying a wider CAB index for unresolved references.
            opener: Context manager loading a bundle environment and defining its lock lifetime.
            resolver: On-demand CAB lookup receiving requested names and an optional owning-bundle hint.
        """
        self.index = {cab: str(path) for cab, path in index.items()}
        for path in overrides:
            for cab in cab_names(Path(path)):
                self.index[cab] = str(path)
        self.fallback = fallback
        self.resolver = resolver
        self.locations: dict[str, str] = {}
        self._unresolved: set[str] = set()
        self.opener = opener
        self.objects: dict[tuple[str, int], Any] = {}
        self.trees: dict[tuple[str, int], Any] = {}
        self.loaded: list[str] = []
        self.envs: dict[str, Any] = {}  # the loaded bundles' environments, by path (their containers)
        # Missing external CABs can arrive later without changing any already-read
        # file. Partial exports remain usable, but cannot be persisted as complete.
        self.missing_cabs: set[str] = set()
        self._stack = ExitStack()

    def __enter__(self) -> "BundleGraph":
        """Return this graph for scoped bundle lifetime management."""
        return self

    def __exit__(self, *exc: Any) -> None:
        """Release loaded bundle contexts when graph traversal exits."""
        self.close()

    def close(self) -> None:
        """Release bundle contexts and discard object, typetree, and lookup references."""
        self.objects.clear()
        self.trees.clear()
        self.locations.clear()
        self.envs.clear()
        self._unresolved.clear()
        self._stack.close()

    def add(self, path: Path | str) -> Any:
        """Load a bundle once and index its objects by CAB and path ID.

        Args:
            path: Bundle location in the same path spelling used by the graph's index.

        Returns:
            The loaded environment, or None when this path was already loaded.
        """
        path = str(path)
        if path in self.loaded:
            return None
        self.loaded.append(path)
        env = self._stack.enter_context(self.opener(Path(path)))
        self.envs[path] = env
        for obj in env.objects:
            self.objects[_key(obj)] = obj
            self.locations[obj.assets_file.name.lower()] = path
        return env

    def tree(self, obj: Any) -> Any:
        """Read and cache a Unity object's typetree by its CAB/path-ID identity."""
        key = _key(obj)
        tree = self.trees.get(key)
        if tree is None:
            tree = self.trees[key] = obj.read_typetree()
        return tree

    def deref(self, obj: Any, pointer: Mapping[str, int] | None) -> Any:
        """Resolve an object pointer, loading its CAB's bundle on demand.

        Args:
            obj: Pointer owner supplying its serialized file's external table; None resolves to None.
            pointer: m_FileID/m_PathID reference; None or a zero path ID resolves to None.

        Returns:
            The referenced reader, or None when the CAB or object cannot be found.
        """
        if obj is None or not pointer or not pointer.get("m_PathID"):
            return None
        cab, pid = _external_cab(obj, pointer.get("m_FileID", 0)), pointer["m_PathID"]
        hit = self.objects.get((cab, pid))
        if hit is None:
            if (
                cab not in self.index
                and self.resolver is not None
                and cab.startswith("cab-")
                and cab not in self._unresolved
            ):
                owner = self.locations.get(obj.assets_file.name.lower())
                found = self.resolver({cab}, Path(owner) if owner else None)
                self.index = {**found, **self.index}
                if cab not in self.index:
                    self._unresolved.add(cab)
            if cab not in self.index and self.fallback is not None and cab.startswith("cab-"):
                logger.debug("CAB %s is not in the model index; scanning every bundle", cab)
                wider, self.fallback = self.fallback(), None
                self.index = {**{c: str(p) for c, p in wider.items()}, **self.index}
            path = self.index.get(cab)
            if path is not None and path not in self.loaded:
                self.add(path)
                hit = self.objects.get((cab, pid))
            elif path is None and cab.startswith("cab-"):
                self.missing_cabs.add(cab)
        return hit

    def component(self, game_object: Any, type_name: str) -> Any:
        """Find the first component of a Unity type on a GameObject.

        Args:
            game_object: GameObject reader whose m_Component pointers are followed.
            type_name: Exact Unity object type name, such as Transform or Animator.

        Returns:
            The component reader, or None when no component matches.
        """
        for c in self.tree(game_object)["m_Component"]:
            obj = self.deref(game_object, c["component"])
            if obj is not None and obj.type.name == type_name:
                return obj
        return None

    def script(self, behaviour: Any) -> str | None:
        """Return a MonoBehaviour reader's script class name, or None for an unresolved script."""
        script = self.deref(behaviour, self.tree(behaviour).get("m_Script"))
        return self.tree(script).get("m_ClassName") if script is not None else None


# --- the exporter ---------------------------------------------------------------------------------------------------


def _color(c: Mapping[str, float]) -> list[float]:
    return [round(c["r"], 5), round(c["g"], 5), round(c["b"], 5), round(c["a"], 5)]


def _vec3(v: Mapping[str, float]) -> list[float]:
    return [v["x"], v["y"], v["z"]]


# glTF sampler constants
_NEAREST, _LINEAR = 9728, 9729
_NEAREST_MIPMAP_NEAREST, _LINEAR_MIPMAP_NEAREST, _LINEAR_MIPMAP_LINEAR = 9984, 9985, 9987
_WRAPS = {0: 10497, 1: 33071, 2: 33648, 3: 33071}  # Unity Repeat, Clamp, Mirror, MirrorOnce


def texture_sampler(tree: Mapping[str, Any]) -> dict[str, int]:
    """Convert Texture2D sampler settings to glTF without generating absent mipmaps.

    Preserve point, bilinear or trilinear filtering,
    mipmaps only when the texture has them (the characters' textures have none, a viewer that generated them would
    blur and darken thin details at a distance), and its wrap modes. Point takes the nearest texel of the nearest
    mipmap, bilinear blends texels within the nearest mipmap, trilinear blends two mipmaps as well.

    Args:
        tree: Texture2D typetree with m_TextureSettings and m_MipCount.
    """
    s = tree.get("m_TextureSettings") or {}
    mode = s.get("m_FilterMode", 1)
    point = mode == 0
    mips = (tree.get("m_MipCount") or 1) > 1
    if mips:
        minify = _NEAREST_MIPMAP_NEAREST if point else _LINEAR_MIPMAP_LINEAR if mode == 2 else _LINEAR_MIPMAP_NEAREST
    else:
        minify = _NEAREST if point else _LINEAR
    sampler = {"magFilter": _NEAREST if point else _LINEAR, "minFilter": minify}
    wrap = s.get("m_WrapMode", 0)
    sampler["wrapS"] = _WRAPS.get(s.get("m_WrapU", wrap), 10497)
    sampler["wrapT"] = _WRAPS.get(s.get("m_WrapV", wrap), 10497)
    return sampler


def shader_vectors(form: Mapping[str, Any]) -> set[str]:
    """Return Vector property names (m_Type 1) from a parsed shader form.

    Materials store Color and Vector values in m_Colors, but only Colors receive sRGB-to-linear conversion.

    Args:
        form: Parsed shader form whose m_PropInfo declarations distinguish Vector from Color storage.
    """
    return {p["m_Name"] for p in form["m_PropInfo"]["m_Props"] if p.get("m_Type") == 1}


_HDR = 16  # ShaderPropertyFlags.HDR


def shader_hdr(form: Mapping[str, Any]) -> set[str]:
    """Return HDR Color property names (m_Type 0 with the HDR flag) from a parsed shader form.

    Their stored values are linear already:
    the game passes them unconverted, like Vector properties. The character shaders mark most of their colours HDR
    (the hair's and face's _ShadowTint, _Tint, _OutlineTint, the halos' tints). sRGB decoding makes hair shadows
    about half as bright and redder than EnemyInfo renders, and Aru's HDR halo tint (3.69, 1.76, 1.53)
    near white instead of coral.

    Args:
        form: Parsed shader form whose Color declarations carry ShaderPropertyFlags.HDR.
    """
    return {p["m_Name"] for p in form["m_PropInfo"]["m_Props"] if p.get("m_Type") == 0 and p.get("m_Flags", 0) & _HDR}


def shader_outline(form: Mapping[str, Any]) -> bool:
    """Check a parsed shader form for an inverted-hull pass with light mode "Outline"."""
    for sub in form["m_SubShaders"]:
        for p in sub["m_Passes"]:
            tags = dict(p["m_State"]["m_Tags"]["tags"])
            if tags.get("LIGHTMODE", "").lower() == "outline":
                return True
    return False


# passes that do not draw the colour: the outline, shadow and depth passes
_NOT_FORWARD = {"outline", "outlinesolidcolor", "shadowcaster", "depthonly", "srpdefaultunlit", "meta"}
_QUEUES = {"background": 1000, "geometry": 2000, "alphatest": 2450, "transparent": 3000, "overlay": 4000}


def shader_state(form: Mapping[str, Any], floats: Mapping[str, float], custom_queue: int = -1) -> dict[str, Any]:
    """Read cull, depth-write, blend, and queue state for a material's shader color pass.

    A state either
    names a material property (``_Cull``, ``_SrcBlend`` ...: the material's float decides) or is fixed in the pass.
    Cull 0 off, 1 front, 2 back; blend factors as UnityEngine.Rendering.BlendMode (1 One, 0 Zero, 5 SrcAlpha,
    10 OneMinusSrcAlpha).

    Args:
        form: Parsed shader form, preferring UniversalForward then other color passes.
        floats: Material properties used when a pass state names one.
        custom_queue: Material render queue; a negative value or None derives it from shader tags.
    """

    def tags(entry: Mapping[str, Any]) -> dict[str, str]:
        return {k.lower(): v for k, v in (entry.get("m_Tags") or {}).get("tags", [])}

    passes = [p for sub in form["m_SubShaders"] for p in sub["m_Passes"]]
    mode = lambda p: tags(p["m_State"]).get("lightmode", "").lower()  # noqa: E731
    forward = (
        [p for p in passes if mode(p) == "universalforward"]
        or [p for p in passes if mode(p) not in _NOT_FORWARD]
        or passes
    )
    state = forward[0]["m_State"]
    blend = state.get("rtBlend0") or state["rtBlend"][0]

    def value(entry: Mapping[str, Any]) -> float:
        name = entry.get("name", "")
        return float(floats[name]) if name in floats else float(entry["val"])

    queue = custom_queue
    if queue is None or queue < 0:
        spec = tags(state).get("queue") or tags(form["m_SubShaders"][0]).get("queue") or "Geometry"
        base, _, offset = spec.partition("+")
        queue = _QUEUES.get(base.strip().lower(), 2000) + (int(offset) if offset.strip().isdigit() else 0)
    return {
        "cull": int(value(state["culling"])),
        "zwrite": int(value(state["zWrite"])),
        "src": int(value(blend["srcBlend"])),
        "dst": int(value(blend["destBlend"])),
        "queue": int(queue),
    }


# Vector properties of the character shaders, for a material whose shader cannot be read
_VECTOR_NAME = re.compile(r"4$|Dir")


def _prefabs(graph: BundleGraph, bundles: Iterable[Path]) -> dict[str, Any]:
    """Prefab roots of the prefab bundles by lower-case stem (Echelon_CH0242, CH0242, ...)."""
    found = {}
    for path in bundles:
        graph.add(path)
        env = graph.envs.get(str(path))  # also when an earlier lookup loaded it
        for name, pointer in env.container.items() if env is not None else ():
            lower = name.lower()
            if lower.endswith(".prefab") and "/character/" in lower:
                found[lower.rsplit("/", 1)[-1][:-7]] = pointer
    return found


def npc_prefab(stems: Iterable[str], dev: str) -> str | None:
    """Choose a field NPC prefab, preferring a halo and then the first name.

    NP0143's AR weapon precedes its SG and SR prefabs.

    Args:
        stems: Available lower-case prefab filename stems.
        dev: NPC character ID such as np0060, matched without case sensitivity.

    Returns:
        The preferred stem, or None when no field NPC prefab matches.
    """
    dev = dev.lower()
    hits = [stem for stem in stems if (match := NPC_PREFAB.fullmatch(stem)) and match.group(1).lower() == dev]
    return min(hits, key=lambda stem: ("nohalo" in stem.lower(), stem.lower()), default=None)


def _subtree_renderers(graph: BundleGraph, transform: Any) -> int:
    """The renderers (skinned and static) at or below a Transform."""
    count, stack = 0, [transform]
    while stack:
        tr = stack.pop()
        tree = graph.tree(tr)
        go = graph.deref(tr, tree.get("m_GameObject"))
        if go is not None:
            count += sum(
                1
                for c in graph.tree(go)["m_Component"]
                if (obj := graph.deref(go, c["component"])) is not None
                and obj.type.name in ("SkinnedMeshRenderer", "MeshRenderer")
            )
        stack.extend(
            child for child in (graph.deref(tr, pointer) for pointer in tree.get("m_Children", [])) if child is not None
        )
    return count


def asset_root(graph: BundleGraph, bundles: Iterable[Path], dev: str | None = None) -> tuple[Any, str] | None:
    """Find the best model root in a character's or NPC's own dependency bundles.

    Story/UI-only variants, such as Ayame's story forms and Arona, keep rendered container
    roots in assets-_mx-npcs|characters-<dev>-_mxdependency-* bundles. Choose the first asset_roots result.

    Args:
        graph: Dependency graph loading bundle containers and following model references.
        bundles: Candidate character or NPC dependency bundles to inspect.
        dev: Optional dev name restricting the search to that character's own bundles.

    Returns:
        Root GameObject and container path, or None when no rendered model is found.
    """
    found = asset_roots(graph, bundles, dev)
    return (found[0][1], found[0][2]) if found else None


def asset_roots(graph: BundleGraph, bundles: Iterable[Path], dev: str | None = None) -> list[tuple[tuple, Any, str]]:
    """Rank rendered roots for asset_root as (rank, root GameObject, container path).

    A prefab precedes an imported model because it carries assigned materials; a root with an Animator precedes one
    without, then the most renderers, then the name; the ranks of several bundles compare the same way.

    Args:
        graph: Dependency graph loading bundle containers and traversing components.
        bundles: Candidate dependency bundles containing prefab or imported FBX container roots.
        dev: Optional dev name restricting the search to its own bundles.
    """
    found: list[tuple[tuple, Any, str]] = []
    seen: set[tuple[str, int]] = set()
    for path in bundles:
        if dev is not None and not own_bundle(Path(path).name, dev):
            continue
        graph.add(path)
        env = graph.envs.get(str(path))
        for name, pointer in env.container.items() if env is not None else ():
            lower = name.lower()
            if not lower.endswith((".prefab", ".fbx")):
                continue
            try:
                go = pointer.deref()
            except (ValueError, KeyError, TypeError):
                continue
            if go is None or go.type.name != "GameObject" or _key(go) in seen:
                continue
            seen.add(_key(go))
            transform = graph.component(go, "Transform")
            if transform is None or graph.tree(transform).get("m_Father", {}).get("m_PathID"):
                continue
            renderers = _subtree_renderers(graph, transform)
            if renderers:
                animator = graph.component(go, "Animator") is not None
                stem = graph.tree(go)["m_Name"].lower()
                found.append(((not lower.endswith(".prefab"), not animator, -renderers, stem), go, name))
    return sorted(found, key=lambda entry: entry[0])


def model_root(graph: BundleGraph, bundles: Iterable[Path], dev: str, prefab: str = "echelon") -> tuple[Any, str, str]:
    """Choose a model's root GameObject, display name, and prefab kind.

    Try the ``prefab`` kind of
    :data:`PREFAB_KINDS` where there is one, else the first of the others (Echelon, battle, cafe, strategy, field
    player), else a field NPC prefab (kind ``npc``), else a model in the bundles of its own (kind ``asset``).

    Args:
        graph: Dependency graph loading candidate prefabs and model assets.
        bundles: Prefab bundles, or the model's own dependency bundles for story/UI assets.
        dev: Character dev name or NPC ID used to construct prefab names.
        prefab: Preferred key from PREFAB_KINDS, such as echelon, battle, cafe, strategy, or field.

    Raises:
        AssetNotFound: No matching prefab or rendered asset root is available.
    """
    bundles = list(bundles)
    prefabs = _prefabs(graph, bundles)
    for kind in [prefab, *(k for k in PREFAB_KINDS if k != prefab)]:
        pointer = prefabs.get(PREFAB_KINDS[kind].format(dev).lower())
        if pointer is not None:
            root = pointer.deref()
            return root, root.peek_name(), kind
    stem = npc_prefab(prefabs, dev)
    if stem is not None:
        root = prefabs[stem].deref()
        return root, root.peek_name(), "npc"
    found = asset_root(graph, bundles, dev)
    if found is not None:
        return found[0], graph.tree(found[0])["m_Name"], "asset"
    raise AssetNotFound(("model", dev))


class _Exporter:
    def __init__(self, graph: BundleGraph, dev: str):
        """Prepare an empty model export for a dependency graph and character dev name."""
        self.g, self.dev = graph, dev
        self.glb = GlbWriter()
        self.nodes = self.glb.json["nodes"]
        self.node_of: dict[tuple[str, int], int] = {}  # Transform -> node
        self.path_of: dict[int, str] = {}  # node -> hierarchy path below the root
        self.renderers: list[tuple[int, Any, Any]] = []  # (node, renderer, GameObject), shown ones only
        # renderers the prefab disables, by node: exported only when a clip enables them (alternative faces)
        self.disabled: dict[int, tuple[int, Any, Any]] = {}
        self.shown: set[int] = set()  # nodes outside inactive and effect subtrees
        self.follower = self.mouth = None
        # every GameObject as particles.build reads a prefab walk (lobby.read_prefab nodes), for the halo's particles
        self.fx_nodes: list[dict[str, Any]] = []
        self.go_of_node: dict[int, int] = {}  # node -> its GameObject's path id
        self.halo_effects: dict[str, Any] | None = None
        self.animancer = None  # a field prefab's FieldAnimancerController: its own clip list
        self.textures: dict[tuple[str, int], int] = {}
        self.materials: dict[tuple[str, int], int] = {}

    def visit(self, go_obj: Any, parent: int | None, path: str, active: bool, shown: bool = True) -> None:
        """Append glTF nodes for a GameObject hierarchy, deferring hidden renderers.

        Inactive objects and effect nodes (particles, the EX camera and
        light) are kept for the clips but marked hidden; renderers below a hidden node are not exported, since the
        viewer never shows them (and their effect materials sit in unrelated bundles). Renderers the prefab disables
        (m_Enabled 0: alternative faces, bodies and props) are kept aside: only a clip that enables one brings it in
        (:meth:`toggles`).

        Args:
            go_obj: Root GameObject reader for this recursive visit.
            parent: Parent glTF node index; None attaches the node to the scene root list.
            path: Hierarchy path below the animated root, empty for the root itself.
            active: Whether all ancestor GameObjects are active.
            shown: Whether ancestors permit exporting visible renderers, excluding effect subtrees.
        """
        g = self.g
        go = g.tree(go_obj)
        transform, components = None, []
        for c in go["m_Component"]:
            obj = g.deref(go_obj, c["component"])
            if obj is None:
                continue
            if obj.type.name in ("Transform", "RectTransform"):
                transform = obj
            else:
                components.append(obj)
        if transform is None:
            return
        t = g.tree(transform)
        p, r, s = t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"]
        node: dict[str, Any] = {"name": go["m_Name"]}
        translation, rotation = mpos((p["x"], p["y"], p["z"])), mquat((r["x"], r["y"], r["z"], r["w"]))
        scale = (s["x"], s["y"], s["z"])
        if any(abs(x) > 1e-7 for x in translation):
            node["translation"] = list(translation)
        if any(abs(a - b) > 1e-7 for a, b in zip(rotation, (0, 0, 0, 1))):
            node["rotation"] = list(quat_norm(rotation))
        if any(abs(x - 1) > 1e-7 for x in scale):
            node["scale"] = list(scale)
        index = len(self.nodes)
        self.nodes.append(node)
        self.node_of[_key(transform)] = index
        self.path_of[index] = path
        if parent is None:
            self.glb.json["scenes"][0]["nodes"].append(index)
        else:
            self.nodes[parent].setdefault("children", []).append(index)
        here = active and bool(go["m_IsActive"])
        fx = [
            (obj.type.name, obj.path_id, g.tree(obj))
            for obj in components
            if obj.type.name in ("ParticleSystem", "ParticleSystemRenderer")
        ]
        local3 = particles.trs(t)
        self.go_of_node[index] = go_obj.path_id
        self.fx_nodes.append(
            {
                "go": go_obj.path_id,
                "parent": self.go_of_node.get(parent) if parent is not None else None,
                "name": go["m_Name"],
                "path": path,
                "active": here,
                "own": bool(go["m_IsActive"]),
                "all": fx,
                "first": {cls: c for cls, _, c in reversed(fx)},
                "pids": {cls: pid for cls, pid, _ in reversed(fx)},
                "t": t,
                "m3": local3,
                "local3": local3,
                "node": index,
            }
        )
        effect = any(obj.type.name in ("ParticleSystem", "Camera", "Light") for obj in components)
        shown = shown and here and not effect
        if shown:
            self.shown.add(index)
        for obj in components:
            kind = obj.type.name
            if kind in ("SkinnedMeshRenderer", "MeshRenderer"):
                if shown and g.tree(obj).get("m_Enabled", 1):
                    self.renderers.append((index, obj, go_obj))
                elif shown:
                    self.disabled[index] = (index, obj, go_obj)
            elif kind == "MonoBehaviour":
                name = g.script(obj)
                if name == "FxFollower":
                    self.follower = (index, obj, g.tree(obj))
                elif name == "CharacterAnimationEventReceiver":
                    self.mouth = (obj, g.tree(obj))
                elif name == "FieldAnimancerController" and self.animancer is None:
                    self.animancer = obj
        if not here or effect:
            node.setdefault("extras", {})["hidden"] = True
        for child in t["m_Children"]:
            child_tr = g.deref(transform, child)
            child_go = g.deref(child_tr, g.tree(child_tr)["m_GameObject"]) if child_tr is not None else None
            if child_go is not None:
                name = g.tree(child_go)["m_Name"]
                self.visit(child_go, index, f"{path}/{name}" if path else name, here, shown)

    def set_aside_ex_only(self) -> None:
        """Defer EX-only renderers until a clip enables them, exporting them hidden at rest."""
        parts = [(node, self.path_of[node], self._parts(renderer, go_obj)) for node, renderer, go_obj in self.renderers]
        aside = ex_only_renderers(parts)
        for entry in [r for r in self.renderers if r[0] in aside]:
            self.renderers.remove(entry)
            self.disabled[entry[0]] = entry

    def _parts(self, renderer: Any, go_obj: Any) -> list[tuple[str, int]]:
        """(shading kind, index count) of each submesh a renderer draws, read without exporting anything."""
        g = self.g
        tree = g.tree(renderer)
        if renderer.type.name == "SkinnedMeshRenderer":
            mesh = g.deref(renderer, tree.get("m_Mesh"))
        else:
            filt = g.component(go_obj, "MeshFilter")
            mesh = g.deref(filt, g.tree(filt).get("m_Mesh")) if filt is not None else None
        subs = g.tree(mesh).get("m_SubMeshes", []) if mesh is not None else []
        parts = []
        for sub, pointer in zip(subs, tree.get("m_Materials", [])):
            material = g.deref(renderer, pointer)
            shader = g.deref(material, g.tree(material).get("m_Shader")) if material is not None else None
            try:
                name = g.tree(shader)["m_ParsedForm"]["m_Name"] if shader is not None else None
            except (KeyError, TypeError, ValueError):
                name = None
            parts.append((shader_kind(name), int(sub.get("indexCount", 0))))
        return parts

    def texture(self, obj: Any) -> int:
        """Embed a Texture2D reader once with its game sampler/color space and return its glTF texture index."""
        key = _key(obj)
        if key not in self.textures:
            data = obj.read()
            buffer = io.BytesIO()
            owner = self.g.locations.get(obj.assets_file.name.lower())
            texture_image(data, Path(owner).name if owner else None).save(buffer, format="PNG", compress_level=1)
            # m_ColorSpace 0: imported without sRGB (masks, the hair direction map), sampled as stored
            linear = getattr(data, "m_ColorSpace", 1) == 0
            sampler = texture_sampler(self.g.tree(obj))
            self.textures[key] = self.glb.image(buffer.getvalue(), data.m_Name, linear=linear, sampler=sampler)
        return self.textures[key]

    def material(self, obj: Any) -> int:
        """Embed a Material reader once, retaining shader state/properties in extras, and return its index."""
        key = _key(obj)
        if key in self.materials:
            return self.materials[key]
        g, tree = self.g, self.g.tree(obj)
        shader_obj = g.deref(obj, tree.get("m_Shader"))
        shader, vectors, hdr, outline, form = None, None, set(), None, None
        if shader_obj is not None:
            try:
                form = g.tree(shader_obj)["m_ParsedForm"]
                shader = form["m_Name"]
                vectors, hdr, outline = shader_vectors(form), shader_hdr(form), shader_outline(form)
            except (KeyError, TypeError, ValueError):
                shader, form = None, None
        props = tree["m_SavedProperties"]
        textures = {}
        for name, env in props["m_TexEnvs"]:
            tex = g.deref(obj, env["m_Texture"])
            if tex is not None and tex.type.name == "Texture2D":
                textures[name] = {
                    "index": self.texture(tex),
                    "scale": [env["m_Scale"]["x"], env["m_Scale"]["y"]],
                    "offset": [env["m_Offset"]["x"], env["m_Offset"]["y"]],
                }
        colors = {k: _color(v) for k, v in props["m_Colors"]}
        floats = {k: round(v, 5) for k, v in props["m_Floats"]}
        keywords = set(tree.get("m_ValidKeywords") or []) | set((tree.get("m_ShaderKeywords") or "").split())
        if vectors is None:
            vectors = {name for name in colors if _VECTOR_NAME.search(name)}
        if outline is None:
            outline = "_OutlineTint" in colors
        # Material.SetShaderPassEnabled: the game turns the outline off on some materials whose shader has one
        # (the MX/C-Hair-Transparent strands, ch0109's fan)
        if any(str(p).lower() == "outline" for p in tree.get("disabledShaderPasses") or []):
            outline = False
        state = None
        if form is not None:
            try:
                state = shader_state(form, floats, tree.get("m_CustomRenderQueue", -1))
            except (KeyError, TypeError, ValueError, IndexError):
                state = None
        material: dict[str, Any] = {
            "name": tree["m_Name"],
            "doubleSided": floats.get("_Cull", 2) == 0,
            "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 1},
            # colors: as the material stores them; vectors and hdr: the ones the game passes through unconverted
            # (Vector properties, HDR Color properties), every other color is converted from sRGB to linear first
            "extras": {
                "shader": shader,
                "kind": shader_kind(shader),
                "colors": colors,
                "vectors": sorted(vectors & set(colors)),
                "hdr": sorted(hdr & set(colors)),
                "floats": floats,
                "textures": textures,
                "keywords": sorted(keywords),
                "outline": outline,
                "state": state,
            },
        }
        if "_MainTex" in textures:
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": textures["_MainTex"]["index"]}
        elif "_Tint" in colors:
            material["pbrMetallicRoughness"]["baseColorFactor"] = [min(1, x) for x in colors["_Tint"][:3]] + [1]
        self.glb.json["materials"].append(material)
        self.materials[key] = len(self.glb.json["materials"]) - 1
        return self.materials[key]

    def meshes(self, renderers: Iterable[tuple[int, Any, Any]] | None = None) -> None:
        """Export renderer geometry, materials, and skins into the current hierarchy.

        Args:
            renderers: Node/renderer/GameObject triples to export; None uses the prefab's visible renderers.
        """
        g, glb, root = self.g, self.glb, self.glb.json["scenes"][0]["nodes"][0]
        for node_index, renderer, go_obj in self.renderers if renderers is None else renderers:
            tree = g.tree(renderer)
            if renderer.type.name == "SkinnedMeshRenderer":
                mesh_obj = g.deref(renderer, tree["m_Mesh"])
            else:
                filt = g.component(go_obj, "MeshFilter")
                mesh_obj = g.deref(filt, g.tree(filt)["m_Mesh"]) if filt is not None else None
            materials = [g.deref(renderer, m) for m in tree["m_Materials"]]
            if mesh_obj is None or any(m is None for m in materials):
                continue  # effect meshes in shared bundles
            mesh = mesh_obj.read()
            data = read_mesh(mesh)
            attrs = {"POSITION": glb.accessor([mpos(v) for v in data.vertices], "VEC3", target=_ARRAY, minmax=True)}
            if data.normals:
                attrs["NORMAL"] = glb.accessor([mpos(v[:3]) for v in data.normals], "VEC3", target=_ARRAY)
            for name, uv in (("TEXCOORD_0", data.uv0), ("TEXCOORD_1", data.uv1)):
                if uv:
                    attrs[name] = glb.accessor([(u[0], 1 - u[1]) for u in uv], "VEC2", target=_ARRAY)
            if data.colors:
                attrs["COLOR_0"] = glb.accessor(
                    [tuple(c[:4]) if len(c) >= 4 else (*c[:3], 1) for c in data.colors], "VEC4", target=_ARRAY
                )
            if data.tangents:
                # OL_ meshes keep the smoothed outline normal in the tangent xyz: a custom attribute, not a tangent
                attrs["_OUTLINE"] = glb.accessor([mpos(t[:3]) for t in data.tangents], "VEC3", target=_ARRAY)
            skinned = renderer.type.name == "SkinnedMeshRenderer" and data.joints and tree["m_Bones"]
            if skinned:
                attrs["JOINTS_0"] = glb.accessor(data.joints, "VEC4", component=5123, target=_ARRAY)
                attrs["WEIGHTS_0"] = glb.accessor(data.weights, "VEC4", target=_ARRAY)
            component = 5123 if len(data.vertices) < 65536 else 5125
            primitives = []
            for i, tris in enumerate(data.triangles[: len(materials)]):
                flat = [k for a, b, c in tris for k in (a, c, b)]  # mirrored: flip the winding
                primitives.append(
                    {
                        "attributes": attrs,
                        "material": self.material(materials[i]),
                        "indices": glb.accessor(flat, "SCALAR", component=component, target=_INDICES),
                    }
                )
            glb.json["meshes"].append({"name": mesh.m_Name, "primitives": primitives})
            mesh_index = len(glb.json["meshes"]) - 1
            if not skinned:
                self.nodes[node_index]["mesh"] = mesh_index
                continue
            joints = []
            for bone in tree["m_Bones"]:
                bone_obj = g.deref(renderer, bone)
                joints.append(self.node_of.get(_key(bone_obj), root) if bone_obj is not None else root)
            skin = {
                "joints": joints,
                "name": mesh.m_Name,
                "inverseBindMatrices": glb.accessor([mirror_matrix(e) for e in g.tree(mesh_obj)["m_BindPose"]], "MAT4"),
            }
            root_bone = g.deref(renderer, tree.get("m_RootBone"))
            if root_bone is not None and _key(root_bone) in self.node_of:
                skin["skeleton"] = self.node_of[_key(root_bone)]
            glb.json["skins"].append(skin)
            # Unity skins with bone.localToWorld * bindpose and ignores the renderer's own transform; glTF uses the
            # mesh node's world matrix at bind time, so skinned meshes go on a new identity node under the root
            mesh_node: dict[str, Any] = {
                "name": self.nodes[node_index]["name"] + "_mesh",
                "mesh": mesh_index,
                "skin": len(glb.json["skins"]) - 1,
            }
            self.nodes.append(mesh_node)
            self.nodes[root].setdefault("children", []).append(len(self.nodes) - 1)
            self.nodes[node_index].setdefault("extras", {})["renderer"] = len(self.nodes) - 1

    def extras(self, prefab: str) -> dict[str, Any]:
        """Build root metadata for the named prefab, including halo following, halo effects, and mouth UVs."""
        extras: dict[str, Any] = {"prefab": prefab, "dev": self.dev}
        if self.follower:
            index, obj, tree = self.follower
            target = self.g.deref(obj, tree.get("FollowTarget"))
            lo, hi = tree["ClampMinOffset"], tree["ClampMaxOffset"]
            rot = tree["TargetRelativeRotation"]
            extras["haloFollower"] = {
                "node": index,
                "target": self.node_of.get(_key(target)) if target is not None else None,
                "relativePosition": list(mpos(_vec3(tree["TargetRelativePosition"]))),
                "relativeRotation": list(mquat([rot[k] for k in "xyzw"])),
                # mirroring X swaps the bounds
                "clampMin": [-hi["x"], lo["y"], lo["z"]],
                "clampMax": [-lo["x"], hi["y"], hi["z"]],
                "positionPower": tree["FollowPositionPower"],
                "rotationPower": tree["FollowRotationPower"],
            }
        if self.halo_effects:
            extras["haloFx"] = self.halo_effects
        if self.mouth:
            _, tree = self.mouth
            extras["mouth"] = {
                "materialIndex": tree.get("MouthMaterialIndex"),
                "defaultOffset": [tree["MouthDefaultUV"]["x"], tree["MouthDefaultUV"]["y"]],
            }
        return extras

    def halo_fx(self, resolver: Callable[[Mapping[int, Any]], Callable[[str, int, dict], Any]]) -> None:
        """Export particle systems below the halo's FxFollower for the model viewer.

        Read them as particles.build does for the
        lobbies' player, for the viewer to simulate and draw where their nodes are (Tsurugi's drops, Mika's stars and
        rings): each system names its glTF node ("node") instead of a place in a lobby, and each texture its glTF
        texture ("texture"). resolver(objects of the prefab's file by path id) reads the materials and meshes the
        renderers point at (service._FxResolver). Effects elsewhere in the prefab (an EX skill's, a pickup motion's)
        are left out, as the viewer never plays them.

        Args:
            resolver: Builds a service._FxResolver-compatible material/mesh reader from prefab objects by path ID.
        """
        if not self.follower:
            return
        top = self.path_of[self.follower[0]]
        nodes = [n for n in self.fx_nodes if top and (n["path"] == top or n["path"].startswith(top + "/"))]
        if not any("ParticleSystem" in n["first"] for n in nodes):
            return
        nodes = [{**n, "parent": None} if i == 0 else n for i, n in enumerate(nodes)]
        owner = self.follower[1].assets_file
        found = particles.build(nodes, lambda go: None, lambda skeleton: 1.0, resolver(dict(owner.objects)))
        if not found["systems"]:
            return
        node_of_go = {n["go"]: n["node"] for n in nodes}
        textures = {}
        for tex_id, meta in found["textures"].items():
            cab, _, pid = tex_id.rpartition("_")
            obj = self.g.objects.get((cab, int(pid))) if pid.lstrip("-").isdigit() else None
            if obj is None or obj.type.name != "Texture2D":
                continue
            try:
                textures[tex_id] = {**meta, "texture": self.texture(obj)}
            except Exception:  # an unreadable texture: the systems that sample it are not drawn
                logger.debug("Cannot read halo particle texture %s", tex_id, exc_info=True)
        # placed by their nodes, live: the lobby's places (anchor, rel, static, z) do not apply
        systems = [
            {
                **{k: v for k, v in s.items() if k not in ("anchor", "rel", "static", "z", "_go")},
                "node": node_of_go[s["_go"]],
            }
            for s in found["systems"]
        ]
        self.halo_effects = {
            "systems": systems,
            "materials": found["materials"],
            "textures": textures,
            "meshes": found["meshes"],
        }

    def controller_clips(self, prefabs: Mapping[str, Any]) -> list[Any]:
        """Collect unique clips from prefab controllers and nearby override controllers.

        Empty overrides retain original clips because NPCs share generic clips.

        Args:
            prefabs: Prefab stems mapped to root pointers whose Animator controllers are read.
        """
        g, pointers = self.g, []

        def add(controller: Any) -> None:
            if controller is None:
                return
            tree = g.tree(controller)
            if controller.type.name == "AnimatorOverrideController":
                pointers.extend(
                    (controller, c["m_OverrideClip"] if c["m_OverrideClip"].get("m_PathID") else c["m_OriginalClip"])
                    for c in tree["m_Clips"]
                )
            elif controller.type.name == "AnimatorController":
                pointers.extend((controller, c) for c in tree.get("m_AnimationClips", []))

        for pointer in prefabs.values():
            animator = g.component(pointer.deref(), "Animator")
            if animator is not None:
                add(g.deref(animator, g.tree(animator)["m_Controller"]))
        for obj in list(g.objects.values()):
            if obj.type.name == "AnimatorOverrideController":
                add(obj)
        clips, seen = [], set()
        for owner, pointer in pointers:
            clip = g.deref(owner, pointer)
            if clip is not None and _key(clip) not in seen:
                seen.add(_key(clip))
                clips.append(clip)
        return clips

    def bound_clips(self, graph: BundleGraph, bundles: Iterable[Path]) -> list[Any]:
        """Rank clips moving this hierarchy by their number of bound nodes.

        Story/UI models lack controllers; their bundles can mix character and prop clips,
        such as Arona's sack, so binding coverage distinguishes them.

        Args:
            graph: Graph containing the loaded AnimationClip readers to inspect.
            bundles: Bundle paths restricting clip ownership to the model or selected cutscene sources.
        """
        paths = {str(path) for path in bundles}
        by_hash = {zlib.crc32(path.encode("utf-8")) for path in self.path_of.values()}
        scored = []
        for obj in list(graph.objects.values()):
            if obj.type.name != "AnimationClip" or graph.locations.get(obj.assets_file.name.lower()) not in paths:
                continue
            try:
                bindings = obj.read_typetree()["m_ClipBindingConstant"]["genericBindings"]
            except (KeyError, TypeError, ValueError):
                continue
            bound = len({b["path"] for b in bindings} & by_hash)
            if bound:
                scored.append((-bound, obj.peek_name() or "", obj))
        return [obj for *_, obj in sorted(scored, key=lambda entry: entry[:2])]

    def animations(self, clips: list[Any], wanted: Iterable[str]) -> None:
        """Export each selected clip once under its requested animation slot.

        Args:
            clips: Available AnimationClip readers whose names are matched by pick_clips.
            wanted: Slot suffixes to select, with aliases and a fallback idle.
        """
        names = [c.peek_name() for c in clips]
        exported = set()
        for index, slot in pick_clips(names, wanted, self.dev):
            if names[index] in exported:
                continue
            exported.add(names[index])
            self.animation(clips[index], slot)

    def story(self, clips: list[Any]) -> None:
        """Export at most STORY_CLIPS cutscene clips as Story_1, Story_2, ... in name order.

        A story model has no standard clips to stand in; names include NP0206_Cutin_A_01.

        Args:
            clips: Model-bound cutscene AnimationClip readers already filtered to the character's own names.
        """
        named = sorted(clips, key=lambda clip: clip.peek_name() or "")
        for k, clip in enumerate(named[:STORY_CLIPS], 1):
            self.animation(clip, f"Story_{k}")

    def animation(self, clip: Any, slot: str) -> None:
        """Export a clip's mirrored tracks, mouth events, and renderer toggles.

        Args:
            clip: AnimationClip reader whose generic bindings target the exported hierarchy.
            slot: Viewer animation role; Story_ prefixes also recenter horizontal root motion.
        """
        glb = self.glb
        by_hash = {zlib.crc32(path.encode("utf-8")): index for index, path in self.path_of.items()}
        rest = {
            i: (n.get("translation", [0, 0, 0]), n.get("rotation", [0, 0, 0, 1]), n.get("scale", [1, 1, 1]))
            for i, n in enumerate(self.nodes)
        }
        tt = clip.read_typetree()
        decoded = decode_clip(tt)
        bindings = tt["m_ClipBindingConstant"]["genericBindings"]
        tracks = bind_tracks(bindings, decoded.curves, by_hash)
        orders = euler_orders(bindings, by_hash)
        animation: dict[str, Any] = {"name": tt["m_Name"], "channels": [], "samplers": []}
        times = glb.accessor(decoded.times, "SCALAR", minmax=True)
        zero = None
        for node, attrs in tracks.items():
            for attribute, parts in attrs.items():
                path, values = track_values(attribute, parts, orders.get(node, UNITY_ORDER))
                if slot.startswith("Story_") and path == "translation" and self.path_of.get(node) in STORY_ROOTS:
                    # a cutscene places its character where its shot needs it: the story clip moves about where the
                    # model rests on the ground (its height kept: it may sit or crouch), so the view keeps it framed
                    mean = [sum(value[axis] for value in values) / len(values) for axis in range(3)]
                    shift = [r - m if axis != 1 else 0.0 for axis, (r, m) in enumerate(zip(rest[node][0], mean))]
                    values = [[v + d for v, d in zip(value, shift)] for value in values]
                if all(max(abs(a - b) for a, b in zip(v, values[0])) < 1e-5 for v in values):
                    # constant over the clip: one key, or nothing when it is the rest pose (three.js restores
                    # the original state of bindings no clip uses)
                    rest_value = rest[node][{"translation": 0, "rotation": 1, "scale": 2}[path]]
                    if max(abs(a - b) for a, b in zip(values[0], rest_value)) < 1e-5:
                        continue
                    if zero is None:
                        zero = glb.accessor([0.0], "SCALAR", minmax=True)
                    source, values = zero, values[:1]
                else:
                    source = times
                output = glb.accessor(values, "VEC4" if path == "rotation" else "VEC3")
                animation["samplers"].append({"input": source, "output": output, "interpolation": "LINEAR"})
                animation["channels"].append(
                    {"sampler": len(animation["samplers"]) - 1, "target": {"node": node, "path": path}}
                )
        events = [
            {"time": round(e["time"], 4), "function": e["functionName"], "int": e["intParameter"]}
            for e in tt.get("m_Events", [])
            if e["functionName"] == "SetMouthTile"
        ]
        animation["extras"] = {
            "slot": slot,
            "loop": bool(tt["m_MuscleClip"]["m_LoopTime"]),
            "duration": decoded.duration,
            "events": events,
        }
        toggles = self.toggles(tt, decoded, by_hash)
        if toggles:
            animation["extras"]["toggles"] = toggles
        if animation["channels"]:  # NPC placeholders have no bindings
            glb.json["animations"].append(animation)

    def toggles(self, tt: Mapping[str, Any], decoded: DecodedClip, by_hash: Mapping[int, int]) -> list[dict[str, Any]]:
        """Export renderer switch change points, bringing in disabled renderers when needed.

        Records are {node: mesh node, keys: [[time, 0 | 1], ...]}, such as the
        alternative faces of an expression. A renderer the prefab disables is exported once a clip enables it, hidden
        at rest. Switches equal to the rest state over the whole clip are left out.

        Args:
            tt: AnimationClip typetree supplying generic bindings and start time.
            decoded: Sampled scalar curves and second-based times for this clip.
            by_hash: Hierarchy-path CRC32 values mapped to exported node indices.
        """
        start = tt["m_MuscleClip"]["m_StartTime"]
        out: dict[int, list[list[float]]] = {}
        for node, kind, values in bind_toggles(tt["m_ClipBindingConstant"]["genericBindings"], decoded.curves, by_hash):
            if node not in self.shown:
                continue  # below an inactive or effect object: nothing of it is exported
            keys = toggle_keys(values, decoded.times, start)
            if kind == "renderer":
                if node in self.disabled and any(state for _, state in keys):
                    self.meshes([self.disabled.pop(node)])
                    self._hide_renderer(node)
                nodes = [node]
            else:  # a GameObject: every exported renderer at or below it
                nodes = self._subtree(node)
            for target in [t for t in map(self._renderer_node, nodes) if t is not None]:
                rest = self._rest(target)
                if any(state != rest for _, state in keys):
                    out[target] = keys
        return [{"node": node, "keys": keys} for node, keys in sorted(out.items())]

    def _renderer_node(self, node: int) -> int | None:
        """The node that draws a GameObject's renderer: its skinned mesh node, or the node itself for a static mesh."""
        extras = self.nodes[node].get("extras", {})
        if "renderer" in extras:
            return extras["renderer"]
        return node if "mesh" in self.nodes[node] else None

    def _hide_renderer(self, node: int) -> None:
        target = self._renderer_node(node)
        if target is not None:
            self.nodes[target].setdefault("extras", {})["hidden"] = True

    def _rest(self, target: int) -> int:
        """A renderer node's state at rest: 0 when hidden, else 1."""
        return 0 if self.nodes[target].get("extras", {}).get("hidden") else 1

    def _subtree(self, node: int) -> list[int]:
        out, stack = [], [node]
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(c for c in self.nodes[n].get("children", []) if c in self.path_of)
        return out


class _ClipExporter(_Exporter):
    """Export one model clip for a viewer that already shows its base GLB.

    The base GLB is :func:`export_chibi` of the same files; the clip has no meshes, textures or halo effects of its
    own. The hierarchy's nodes come in the base GLB's order, so a
    channel's node is the same node there. Toggles name the node of the renderer's GameObject (the viewer finds its
    renderer in the base GLB: the node's ``extras.renderer`` or its mesh); a renderer the base GLB leaves out
    (disabled, or only an EX skill shows it) that the clip turns on comes along with its materials and textures, as
    the base GLB would carry it: its node's ``extras.renderer`` (skinned) or ``mesh`` (static), hidden at rest.
    """

    def __init__(self, graph: BundleGraph, dev: str):
        """Prepare a clip export using the base model's graph and character dev name."""
        super().__init__(graph, dev)
        self.drawn: set[int] = set()  # GameObject nodes of the renderers the base GLB draws
        self.brought: set[int] = set()  # GameObject nodes of the renderers this clip brings in

    def _renderer_node(self, node: int) -> int | None:
        return node if node in self.drawn or node in self.brought else None

    def _hide_renderer(self, node: int) -> None:
        self.brought.add(node)

    def _rest(self, target: int) -> int:
        return 0 if target in self.brought else 1


def _model_clips(exporter: _Exporter, graph: BundleGraph, bundles: list[Path], kind: str) -> list[Any]:
    """Return the clips a model plays.

    Use a field prefab's own list, the clips of a model of its own bundles that move it, else its animator
    controllers' clips.
    """
    if exporter.animancer is not None:
        # a field prefab lists its clips itself; its bundle holds other students' prefabs and controllers
        listed = graph.tree(exporter.animancer).get("animationClips", [])
        return [c for c in (graph.deref(exporter.animancer, p) for p in listed) if c is not None]
    if kind == "asset":
        return exporter.bound_clips(graph, bundles)
    return exporter.controller_clips(_prefabs(graph, bundles))


def export_chibi(
    graph: BundleGraph,
    bundles: Iterable[Path],
    dev: str,
    *,
    prefab: str = "echelon",
    clips: Iterable[str] = DEFAULT_CLIPS,
    story: Iterable[Path] = (),
    fx: Callable[[Mapping[int, Any]], Callable[[str, int, dict], Any]] | None = None,
) -> bytes:
    """Export a chibi's hierarchy, meshes, materials, textures, and chosen clips to GLB bytes.

    The prefab is the Echelon
    (formation) one where there is one, else the battle, cafe or strategy one, else a field-mode player or NPC (an
    event or story variant with no battle model), played with the clips its FieldAnimancerController lists, else a
    model of its own dependency bundles (:func:`asset_root`; ``bundles`` are those bundles), played with an idle among
    their clips that moves it, if any; else with its own clips of the story cutscenes in ``story`` (bundles holding
    clips named after it, ``<dev>_...``, as Story_1, Story_2, ...), so it never stands in its bind pose where the game
    moves it (Ayame's story forms). ``fx`` reads the halo's particle materials and meshes
    (:meth:`_Exporter.halo_fx`); without it the halo's particles are left out.

    Args:
        graph: Dependency graph resolving the model's external CAB references for the export lifetime.
        bundles: Prefab bundles of the dev name, or its own dependency bundles for story/UI models.
        dev: Character dev name or NPC ID selecting the model and its own clips.
        prefab: Preferred PREFAB_KINDS key before fallback roots are tried.
        clips: Animation slot suffixes to include, using aliases and a fallback idle when needed.
        story: Cutscene bundles supplying <dev>_... clips as Story_1, Story_2, ... when an asset model lacks an idle.
        fx: Builds a material/mesh resolver from halo component owners; None omits halo particles.
    """
    bundles = list(bundles)
    root, name, kind = model_root(graph, bundles, dev, prefab)
    exporter = _Exporter(graph, dev)
    exporter.visit(root, None, "", True)
    exporter.set_aside_ex_only()
    exporter.meshes()
    if fx is not None:
        exporter.halo_fx(fx)
    exporter.glb.json["extras"] = {**exporter.extras(name), **({"source": kind} if kind in ("npc", "asset") else {})}
    chosen = _model_clips(exporter, graph, bundles, kind)
    # every clip the model plays, by name: the viewer lists the model's own and asks for one by name (export_clip)
    exporter.glb.json["extras"]["clips"] = list(dict.fromkeys(n for c in chosen if (n := c.peek_name())))
    exporter.animations(chosen, clips)
    story = list(story)
    if kind == "asset" and story and not exporter.glb.json.get("animations"):
        for path in story:
            graph.add(path)
        prefix = f"{dev}_".lower()
        exporter.story(
            [clip for clip in exporter.bound_clips(graph, story) if (clip.peek_name() or "").lower().startswith(prefix)]
        )
    data = exporter.glb.finish()
    logger.debug("model %s (%s): %d bundles, %d KB", dev, name, len(graph.loaded), len(data) // 1024)
    return data


def export_clip(graph: BundleGraph, bundles: Iterable[Path], dev: str, clip: str, *, prefab: str = "echelon") -> bytes:
    """Export one named chibi clip for a viewer already holding the model's base GLB.

    The prefab is chosen and walked as
    :func:`export_chibi` does for the same ``bundles``. The root extras name the clip and the number of hierarchy
    nodes (``hierarchy``), the nodes the viewer matches with the base GLB's. Meshes already in the base are omitted;
    renderers activated only by this clip accompany it as described by _ClipExporter.

    Args:
        graph: Dependency graph resolving the same sources as the base GLB.
        bundles: Prefab or own-dependency bundles used for the base export.
        dev: Character dev name or NPC ID selecting the same hierarchy as the base export.
        clip: Exact clip asset name listed in the base GLB's extras.clips.
        prefab: Preferred PREFAB_KINDS key, matching the base export's choice.

    Raises:
        AssetNotFound: The model has no root or does not play the requested clip.
    """
    bundles = list(bundles)
    root, name, kind = model_root(graph, bundles, dev, prefab)
    exporter = _ClipExporter(graph, dev)
    exporter.visit(root, None, "", True)
    exporter.set_aside_ex_only()
    exporter.drawn = {node for node, _, _ in exporter.renderers}
    hierarchy = len(exporter.nodes)
    found = next((c for c in _model_clips(exporter, graph, bundles, kind) if c.peek_name() == clip), None)
    if found is None:
        raise AssetNotFound(("clip", dev, clip))
    exporter.animation(found, clip)
    exporter.glb.json["extras"] = {"prefab": name, "dev": dev, "clip": clip, "hierarchy": hierarchy}
    data = exporter.glb.finish()
    logger.debug("clip %s of %s (%s): %d bundles, %d KB", clip, dev, name, len(graph.loaded), len(data) // 1024)
    return data
