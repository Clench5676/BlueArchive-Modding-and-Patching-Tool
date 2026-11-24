"""A memorial lobby camera that leaves the intro as the game plays it.

The camera is a node of its own between the prefab's root and the memorial lobby's scale targets
(``memorial_lobby_camera``): its
serialized transform is the camera, which the memorial lobby shows at rest, in its dialogues and whenever the game
skips the
intro. During the intro a track of the intro's timeline holds that node at the official framing and moves it to the
camera when the rest pose starts, with the blend the timeline's own Spine track uses there (the ``Idle_01`` clip's mix).

The timeline asset lives in a small bundle of the memorial lobby's art. The camera writes only its prefab: the prefab
gets its
own copy of the TimelineAsset, which lists the art's tracks (in their own file) and one track of its own, an
AnimationTrack bound to an Animator on the root, whose recorded clip moves the camera node. Every other object of both
files stays as it is, so the camera works on whatever art holds the same timeline.
"""

from __future__ import annotations

import copy
import struct
import zlib
from collections.abc import Callable
from typing import Any

FLT_MAX = 3.4028234663852886e38
REST = "Idle_01"
# m_MuscleClipSize of a generic clip of streamed curves only: a fixed part, 4 bytes per streamed word and 8 per curve
# (checked on the 406 recorded clips of the official memorial lobby timelines)
_CLIP_BASE, _CLIP_WORD, _CLIP_CURVE = 2528, 4, 8
_POSITION, _SCALE = 1, 3


def ptr(pid: int, file_id: int = 0) -> dict:
    """Build a Unity object pointer from its path ID and zero-local or one-based external file ID."""
    return {"m_FileID": file_id, "m_PathID": pid}


def crc(path: str) -> int:
    """Hash an Animator-relative transform path as Unity's unsigned CRC32 binding key."""
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


class Timeline:
    """The timeline bundle's objects: typetrees read once, MonoScript class names, the TimelineAsset."""

    def __init__(self, objects: dict[int, Any]):
        """Cache timeline typetrees and local MonoScript class names.

        Args:
            objects: Object readers from the timeline's serialized file, indexed by path ID.
        """
        self.objects = objects
        self._trees: dict[int, dict] = {}
        self.scripts = {
            pid: self.tree(pid).get("m_ClassName") for pid, obj in objects.items() if obj.type.name == "MonoScript"
        }

    def tree(self, pid: int) -> dict:
        """Read and cache the typetree of a timeline object's path ID."""
        if pid not in self._trees:
            self._trees[pid] = self.objects[pid].read_typetree()
        return self._trees[pid]

    def cls(self, pid: int) -> str | None:
        """Return a local MonoBehaviour's script class by path ID, or None when unavailable."""
        obj = self.objects.get(pid)
        if obj is None or obj.type.name != "MonoBehaviour":
            return None
        script = self.tree(pid).get("m_Script") or {}
        return self.scripts.get(script.get("m_PathID")) if not script.get("m_FileID") else None

    def rest(self, timeline: int) -> tuple[float, float] | None:
        """Find the earliest noninitial Idle_01 clip's start and nonnegative blend duration in seconds.

        Args:
            timeline: TimelineAsset path ID whose local Spine tracks are searched.

        Returns:
            Start and mix duration, or None when the intro has no transition to a rest pose.
        """
        found = None
        for pointer in self.tree(timeline).get("m_Tracks") or []:
            pid = pointer.get("m_PathID")
            if pointer.get("m_FileID") or self.cls(pid) != "SpineAnimationStateTrack":
                continue
            for c in self.tree(pid).get("m_Clips") or []:
                name = str(c.get("m_DisplayName") or "")
                start = float(c.get("m_Start", 0.0))
                if name.rsplit("-", 1)[-1] != REST or start <= 0.0 or (found is not None and start >= found[0]):
                    continue
                asset = (c.get("m_Asset") or {}).get("m_PathID")
                template = (self.tree(asset).get("template") or {}) if asset in self.objects else {}
                mix = float(template.get("mixDuration", 0.0)) if template.get("useBlendDuration", 1) else 0.0
                found = (start, max(0.0, mix))
        return found


def camera_clip(
    template: dict, path: str, start: tuple[float, ...], end: tuple[float, ...], at: float, blend: float
) -> dict:
    """Create a camera clip that holds the intro framing, blends to the camera, then holds it.

    Args:
        template: Recorded AnimationClip typetree from the same serialized file.
        path: Camera node's transform path relative to the Animator.
        start: Initial x/y/z position followed by x/y/z scale.
        end: Final position and scale in the same order.
        at: Rest-pose start time in seconds, when the transition begins.
        blend: Linear transition duration in seconds; zero switches immediately.
    """
    frames = [(-FLT_MAX, [(i, 0.0, 0.0, 0.0, v) for i, v in enumerate(start)])]
    if blend > 0.0:
        frames.append((at, [(i, 0.0, 0.0, (b - a) / blend, a) for i, (a, b) in enumerate(zip(start, end))]))
    frames.append((at + blend, [(i, 0.0, 0.0, 0.0, v) for i, v in enumerate(end)]))
    frames.append((FLT_MAX, []))
    data = b"".join(
        struct.pack("<fi", t, len(keys)) + b"".join(struct.pack("<i4f", *key) for key in keys) for t, keys in frames
    )
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    clip = copy.deepcopy(template)
    muscle = clip["m_MuscleClip"]
    muscle["m_Clip"]["data"] = {
        "m_StreamedClip": {"data": words, "curveCount": len(start)},
        "m_DenseClip": {
            "m_FrameCount": 0,
            "m_CurveCount": 0,
            "m_SampleRate": float(template.get("m_SampleRate", 30.0)),
            "m_BeginTime": 0.0,
            "m_SampleArray": [],
        },
        "m_ConstantClip": {"data": []},
    }
    muscle["m_StartTime"], muscle["m_StopTime"] = 0.0, at + blend
    muscle["m_ValueArrayDelta"] = [{"m_Start": a, "m_Stop": b} for a, b in zip(start, end)]
    muscle["m_ValueArrayReferencePose"] = []
    muscle["m_IndexArray"] = [-1] * len(muscle.get("m_IndexArray") or [])
    binding = {"path": crc(path), "script": ptr(0), "typeID": 4, "customType": 0, "isPPtrCurve": 0}
    clip["m_ClipBindingConstant"] = {
        "genericBindings": [{**binding, "attribute": _POSITION}, {**binding, "attribute": _SCALE}],
        "pptrCurveMapping": [],
    }
    clip.update(
        m_Name="Lobby Camera",
        m_Events=[],
        m_HasGenericRootTransform=False,
        m_HasMotionFloatCurves=False,
        m_MuscleClipSize=_CLIP_BASE + _CLIP_WORD * len(words) + _CLIP_CURVE * len(start),
    )
    if "m_Bounds" in clip:
        clip["m_Bounds"] = {"m_Center": {"x": 0.0, "y": 0.0, "z": 0.0}, "m_Extent": {"x": 0.0, "y": 0.0, "z": 0.0}}
    return clip


class Writer:
    """Copy timeline object types, script types, and external references into a same-build prefab file."""

    def __init__(self, sf: Any, source: Any, timeline_index: int, fresh: Callable[[], int]):
        """Prepare a prefab writer after checking serialized-format compatibility.

        Args:
            sf: Destination prefab serialized file receiving the copied objects.
            source: Timeline serialized file supplying compatible object and script types.
            timeline_index: One-based external-file ID of source in the prefab's file table.
            fresh: Allocates unused nonzero path IDs in the destination.

        Raises:
            ValueError: The serialized files differ in build format or use a header version below 16.
        """
        self.sf, self.source, self.index, self.fresh = sf, source, timeline_index, fresh
        self.trees: dict[int, dict] = {}  # the new objects' typetrees by path id
        if (
            sf.header.version != source.header.version
            or sf.unity_version != source.unity_version
            or sf._enable_type_tree != source._enable_type_tree
            or sf.header.version < 16
        ):
            raise ValueError("timeline of another build")

    def file_id(self, file_id: int) -> int:
        """Map a timeline pointer's file ID into the prefab, adding external entries as needed.

        Args:
            file_id: Zero for a timeline-local object, otherwise its one-based external-file ID.
        """
        if file_id == 0:
            return self.index
        path = self.source.externals[file_id - 1].path
        for i, external in enumerate(self.sf.externals, 1):
            if external.path == path:
                return i
        self.sf.externals.append(copy.copy(self.source.externals[file_id - 1]))
        return len(self.sf.externals)

    def remap(self, value: Any) -> Any:
        """Copy a timeline typetree value with nonnull pointer file IDs remapped to the prefab."""
        if isinstance(value, dict):
            if value.keys() == {"m_FileID", "m_PathID"}:
                return value if not value["m_PathID"] else ptr(value["m_PathID"], self.file_id(value["m_FileID"]))
            return {key: self.remap(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.remap(item) for item in value]
        return value

    def _type_index(self, source_obj: Any) -> int:
        src = source_obj.serialized_type
        if src is None or src.type_dependencies:
            raise ValueError("object type with dependencies")
        if source_obj.class_id == 114:  # a MonoBehaviour: its script's type, the script named in the timeline file
            for i, typ in enumerate(self.sf.types):
                if typ.class_id == 114 and typ.script_id == src.script_id and typ.old_type_hash == src.old_type_hash:
                    return i
            script = self.source.script_types[src.script_type_index]
            entry = object.__new__(type(script))
            entry.local_serialized_file_index = self.file_id(script.local_serialized_file_index)
            entry.local_identifier_in_file = script.local_identifier_in_file
            self.sf.script_types.append(entry)
            typ = copy.copy(src)
            typ.script_type_index = len(self.sf.script_types) - 1
        else:
            for i, typ in enumerate(self.sf.types):
                if typ.class_id == source_obj.class_id and typ.old_type_hash == src.old_type_hash:
                    return i
            typ = copy.copy(src)
        self.sf.types.append(typ)
        return len(self.sf.types) - 1

    def add(self, source_obj: Any, tree: dict) -> int:
        """Add an object of a timeline type to the prefab and return its new path ID.

        Args:
            source_obj: Timeline object reader supplying the serialized and script type.
            tree: New object's typetree, with pointers already mapped into the prefab's file table.
        """
        index = self._type_index(source_obj)
        template = next(iter(self.sf.objects.values()))
        new = copy.copy(template)  # a reader with slots has no __dict__ to copy
        new.type_id, new.serialized_type, new.class_id, new.type = (
            index,
            self.sf.types[index],
            source_obj.class_id,
            source_obj.type,
        )
        new.path_id = self.fresh()
        new.data = b""
        new.save_typetree(tree)
        self.sf.objects[new.path_id] = new
        self.trees[new.path_id] = tree
        ordered = sorted(self.sf.objects.items())
        self.sf.objects.clear()
        self.sf.objects.update(ordered)
        return new.path_id


def add_intro_track(
    writer: Writer,
    timeline: Timeline,
    timeline_id: int,
    node_path: str,
    camera: tuple[tuple[float, ...], tuple[float, ...]],
) -> tuple[int, int, list[int]] | None:
    """Copy an intro TimelineAsset into the prefab with an added camera animation track.

    Args:
        writer: Prefab writer supplying compatible types, remapped pointers, and fresh path IDs.
        timeline: Source timeline's cached objects and script classes.
        timeline_id: Source TimelineAsset path ID whose tracks are retained.
        node_path: Camera transform path relative to the root Animator.
        camera: Initial and final position/scale tuples, each ordered x/y/z position then x/y/z scale.

    Returns:
        New timeline ID, track ID, and all added path IDs; None without a rest transition
        or recorded clip template, leaving the camera framing active during the intro too.
    """
    rest = timeline.rest(timeline_id)
    track_template = next(
        (
            pid
            for pid in timeline.objects
            if timeline.cls(pid) == "AnimationTrack"
            and (timeline.tree(pid).get("m_InfiniteClip") or {}).get("m_PathID") in timeline.objects
        ),
        None,
    )
    if rest is None or track_template is None:
        return None
    clip_obj = timeline.objects[timeline.tree(track_template)["m_InfiniteClip"]["m_PathID"]]
    start, end = camera
    clip = camera_clip(clip_obj.read_typetree(), node_path, start, end, rest[0], rest[1])
    clip_id = writer.add(clip_obj, writer.remap(clip))
    track = writer.remap(copy.deepcopy(timeline.tree(track_template)))
    track.update(m_Name="Lobby Camera", m_InfiniteClip=ptr(clip_id), m_Clips=[], m_Children=[], m_Muted=0, m_Locked=0)
    if "m_Markers" in track:
        track["m_Markers"] = {"m_Objects": []}
    # the new timeline's path id is not known yet: the track's parent is set below
    track_id = writer.add(timeline.objects[track_template], track)
    asset = writer.remap(copy.deepcopy(timeline.tree(timeline_id)))
    asset["m_Tracks"] = [*asset["m_Tracks"], ptr(track_id)]
    asset_id = writer.add(timeline.objects[timeline_id], asset)
    track["m_Parent"] = ptr(asset_id)
    writer.sf.objects[track_id].save_typetree(track)
    writer.trees[track_id] = track
    return asset_id, track_id, [clip_id, track_id, asset_id]
