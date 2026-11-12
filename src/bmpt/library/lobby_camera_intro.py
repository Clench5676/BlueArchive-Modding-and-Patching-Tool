"""A lobby camera that leaves the intro as the game plays it (BL-187).

The camera is a node of its own between the prefab's root and the lobby's scale targets (``lobby_camera``): its
serialized transform is the camera, which the lobby shows at rest, in its dialogues and whenever the game skips the
intro. During the intro a track of the intro's timeline holds that node at the official framing and moves it to the
camera when the rest pose starts, with the blend the timeline's own Spine track uses there (the ``Idle_01`` clip's mix).

The timeline asset lives in a small bundle of the lobby's art. The camera writes only its prefab: the prefab gets its
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
# (checked on the 406 recorded clips of the official lobby timelines)
_CLIP_BASE, _CLIP_WORD, _CLIP_CURVE = 2528, 4, 8
_POSITION, _SCALE = 1, 3


def ptr(pid: int, file_id: int = 0) -> dict:
    return {"m_FileID": file_id, "m_PathID": pid}


def crc(path: str) -> int:
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


class Timeline:
    """The timeline bundle's objects: typetrees read once, MonoScript class names, the TimelineAsset."""

    def __init__(self, objects: dict[int, Any]):
        self.objects = objects
        self._trees: dict[int, dict] = {}
        self.scripts = {pid: self.tree(pid).get("m_ClassName") for pid, obj in objects.items()
                        if obj.type.name == "MonoScript"}

    def tree(self, pid: int) -> dict:
        if pid not in self._trees:
            self._trees[pid] = self.objects[pid].read_typetree()
        return self._trees[pid]

    def cls(self, pid: int) -> str | None:
        obj = self.objects.get(pid)
        if obj is None or obj.type.name != "MonoBehaviour":
            return None
        script = self.tree(pid).get("m_Script") or {}
        return self.scripts.get(script.get("m_PathID")) if not script.get("m_FileID") else None

    def rest(self, timeline: int) -> tuple[float, float] | None:
        """When the rest pose starts in the intro and the mix the Spine track blends into it: the earliest Idle_01 clip
        of a Spine track that does not start the intro. None for a timeline without one."""
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


def camera_clip(template: dict, path: str, start: tuple[float, ...], end: tuple[float, ...], at: float,
                blend: float) -> dict:
    """A recorded clip of the camera node (``path`` below the Animator): position and scale, ``start`` until ``at``,
    then ``end``, reached linearly over ``blend`` seconds; held after. ``template``: a recorded clip of the same file."""
    frames = [(-FLT_MAX, [(i, 0.0, 0.0, 0.0, v) for i, v in enumerate(start)])]
    if blend > 0.0:
        frames.append((at, [(i, 0.0, 0.0, (b - a) / blend, a) for i, (a, b) in enumerate(zip(start, end))]))
    frames.append((at + blend, [(i, 0.0, 0.0, 0.0, v) for i, v in enumerate(end)]))
    frames.append((FLT_MAX, []))
    data = b"".join(struct.pack("<fi", t, len(keys)) + b"".join(struct.pack("<i4f", *key) for key in keys)
                    for t, keys in frames)
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    clip = copy.deepcopy(template)
    muscle = clip["m_MuscleClip"]
    muscle["m_Clip"]["data"] = {
        "m_StreamedClip": {"data": words, "curveCount": len(start)},
        "m_DenseClip": {"m_FrameCount": 0, "m_CurveCount": 0, "m_SampleRate": float(template.get("m_SampleRate", 30.0)),
                        "m_BeginTime": 0.0, "m_SampleArray": []},
        "m_ConstantClip": {"data": []}}
    muscle["m_StartTime"], muscle["m_StopTime"] = 0.0, at + blend
    muscle["m_ValueArrayDelta"] = [{"m_Start": a, "m_Stop": b} for a, b in zip(start, end)]
    muscle["m_ValueArrayReferencePose"] = []
    muscle["m_IndexArray"] = [-1] * len(muscle.get("m_IndexArray") or [])
    binding = {"path": crc(path), "script": ptr(0), "typeID": 4, "customType": 0, "isPPtrCurve": 0}
    clip["m_ClipBindingConstant"] = {"genericBindings": [{**binding, "attribute": _POSITION},
                                                         {**binding, "attribute": _SCALE}],
                                     "pptrCurveMapping": []}
    clip.update(m_Name="Lobby Camera", m_Events=[], m_HasGenericRootTransform=False, m_HasMotionFloatCurves=False,
                m_MuscleClipSize=_CLIP_BASE + _CLIP_WORD * len(words) + _CLIP_CURVE * len(start))
    if "m_Bounds" in clip:
        clip["m_Bounds"] = {"m_Center": {"x": 0.0, "y": 0.0, "z": 0.0}, "m_Extent": {"x": 0.0, "y": 0.0, "z": 0.0}}
    return clip


class Writer:
    """Adds objects to the prefab's file that are made after the timeline file's (same build): their types, script
    types and the external files their pointers name."""

    def __init__(self, sf: Any, source: Any, timeline_index: int, fresh: Callable[[], int]):
        self.sf, self.source, self.index, self.fresh = sf, source, timeline_index, fresh
        self.trees: dict[int, dict] = {}  # the new objects' typetrees by path id
        if (sf.header.version != source.header.version or sf.unity_version != source.unity_version
                or sf._enable_type_tree != source._enable_type_tree or sf.header.version < 16):
            raise ValueError("timeline of another build")

    def file_id(self, file_id: int) -> int:
        """A timeline file's pointer file id in the prefab's file: its own objects are the external timeline file."""
        if file_id == 0:
            return self.index
        path = self.source.externals[file_id - 1].path
        for i, external in enumerate(self.sf.externals, 1):
            if external.path == path:
                return i
        self.sf.externals.append(copy.copy(self.source.externals[file_id - 1]))
        return len(self.sf.externals)

    def remap(self, value: Any) -> Any:
        """A typetree of the timeline file with its pointers as the prefab's file names them."""
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
        """A new object of ``source_obj``'s type with ``tree`` (pointers already the prefab's)."""
        index = self._type_index(source_obj)
        template = next(iter(self.sf.objects.values()))
        new = object.__new__(type(template))
        new.__dict__.update(template.__dict__)
        new.type_id, new.serialized_type, new.class_id, new.type = (index, self.sf.types[index], source_obj.class_id,
                                                                     source_obj.type)
        new.path_id = self.fresh()
        new.data = b""
        new.save_typetree(tree)
        self.sf.objects[new.path_id] = new
        self.trees[new.path_id] = tree
        ordered = sorted(self.sf.objects.items())
        self.sf.objects.clear()
        self.sf.objects.update(ordered)
        return new.path_id


def add_intro_track(writer: Writer, timeline: Timeline, timeline_id: int, node_path: str,
                    camera: tuple[tuple[float, ...], tuple[float, ...]]) -> tuple[int, int, list[int]] | None:
    """The prefab's own copy of the intro's TimelineAsset with the camera track: (timeline, track, added path ids), or
    None when the intro has no rest pose to switch at or no recorded track to copy from (the intro is then framed by
    the camera too)."""
    rest = timeline.rest(timeline_id)
    track_template = next((pid for pid in timeline.objects if timeline.cls(pid) == "AnimationTrack"
                           and (timeline.tree(pid).get("m_InfiniteClip") or {}).get("m_PathID") in timeline.objects),
                          None)
    if rest is None or track_template is None:
        return None
    clip_obj = timeline.objects[timeline.tree(track_template)["m_InfiniteClip"]["m_PathID"]]
    start, end = camera
    clip = camera_clip(clip_obj.read_typetree(), node_path, start, end, rest[0], rest[1])
    clip_id = writer.add(clip_obj, writer.remap(clip))
    track = writer.remap(copy.deepcopy(timeline.tree(track_template)))
    track.update(m_Name="Lobby Camera", m_InfiniteClip=ptr(clip_id), m_Clips=[], m_Children=[], m_Muted=0,
                 m_Locked=0)
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
