"""The GLB side of the 3D chibi export: mesh reading, the mirrored maths, clip decoding and the GLB writer."""

from __future__ import annotations

import json
import math
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import UnityPy.helpers.MeshHelper as MeshHelper
from UnityPy.helpers.MeshHelper import MeshHandler
from UnityPy.helpers.PackedBitVector import reshape, unpack_floats, unpack_ints

# The clips put in the GLB (by name suffix): the formation idle (weapon in hand) is the default loop.
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
