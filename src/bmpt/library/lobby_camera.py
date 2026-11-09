"""A memorial lobby with its own camera (BL-137): the lobby prefab framed anew, kept as a Memorial Lobby choice.

The game scales a lobby by its screen's aspect ratio with the prefab root's ``MX.Utils.UIManualScaleByAspect``: it
multiplies its ``scaleTransforms``' local scale and adds whole units to its ``offsetSettings``' local positions
(``gameassets.lobby.aspect_rule``); nothing else moves those Transforms. A camera of scale ``k`` and offset ``o``
(NGUI units, ``lobby.js cameraParams``) is the similarity ``p -> k p + o`` about the screen centre: the scale targets
scaled by ``k`` and both kinds of target moved to ``k p + o`` (in the root's frame), the component's offsets times
``k``. It holds at every aspect, since the component multiplies the scale and adds the offsets. Only those objects of
the prefab bundle change; every other object and stream stays byte for byte.
"""

from __future__ import annotations

import copy
import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.models import SaveOptions

from ..errors import ServiceError

TAG = "lobby_camera"
SCRIPT = "UIManualScaleByAspect"
_TRANSFORM = "Transform"
_AXES = "xyz"
# the most a camera may scale a lobby by, either way, and move it by: far past what the framing limits allow
SCALE_RANGE = (0.1, 10.0)
OFFSET_LIMIT = 20000.0


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value: Any) -> bool:
    return isinstance(value, dict) and all(_number(value.get(axis)) for axis in _AXES)


def _refuse(root: str) -> ServiceError:
    return ServiceError("lobby_camera_unsupported", name=root)


def check_params(scale: Any, offset: Any) -> tuple[float, tuple[float, float]]:
    """A camera's scale and offset as numbers, or ``bad_params``."""
    if not _number(scale) or not SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]:
        raise ServiceError("bad_params", key="scale")
    if (not isinstance(offset, (list, tuple)) or len(offset) != 2 or not all(_number(v) for v in offset)
            or any(abs(v) > OFFSET_LIMIT for v in offset)):
        raise ServiceError("bad_params", key="offset")
    return float(scale), (float(offset[0]), float(offset[1]))


def camera_trees(kinds: dict[int, str], tree: Callable[[int], dict], scripts: dict[int, str], root: str,
                 scale: float, offset: tuple[float, float]) -> dict[int, dict]:
    """The new typetrees of the objects a camera changes in one lobby prefab, by path id: the root's
    UIManualScaleByAspect and its target Transforms. kinds, tree and scripts as ``lobby.read_prefab`` takes them. A
    prefab without the component on its root, or whose component or targets are not as described above (a target
    that is no plain Transform of this file, a parent between it and the root that is scaled or turned), is refused
    (``lobby_camera_unsupported``): nothing is guessed."""
    scale, (ox, oy) = check_params(scale, offset)
    top = next((pid for pid, kind in kinds.items() if kind == _TRANSFORM and not tree(pid)["m_Father"]["m_PathID"]
                and tree(tree(pid)["m_GameObject"]["m_PathID"])["m_Name"].lower() == root.lower()), None)
    if top is None:
        raise _refuse(root)
    found = []
    for item in tree(tree(top)["m_GameObject"]["m_PathID"]).get("m_Component", []):
        pid = item["component"]["m_PathID"]
        if kinds.get(pid) == "MonoBehaviour":
            behaviour = tree(pid)
            if scripts.get(behaviour.get("m_Script", {}).get("m_PathID")) == SCRIPT:
                found.append(pid)
    if len(found) != 1:
        raise _refuse(root)
    component_id = found[0]
    component = copy.deepcopy(tree(component_id))
    scaled, settings = component.get("scaleTransforms"), component.get("offsetSettings")
    if (not _number(component.get("mostWideScale")) or not _number(component.get("mostNarrowScale"))
            or not isinstance(scaled, list) or not scaled or not isinstance(settings, list)):
        raise _refuse(root)

    def target(pointer: Any) -> int:
        if not isinstance(pointer, dict) or pointer.get("m_FileID") or kinds.get(pointer.get("m_PathID")) != _TRANSFORM:
            raise _refuse(root)
        return pointer["m_PathID"]

    def parents(pid: int) -> tuple[float, float]:
        """The translation of a target's parents up to the root (the root's own Transform is the frame), or a refusal
        when one of them scales or turns."""
        x = y = 0.0
        seen = set()
        father = tree(pid)["m_Father"]["m_PathID"]
        while father != top:
            if father in seen or kinds.get(father) != _TRANSFORM:
                raise _refuse(root)
            seen.add(father)
            t = tree(father)
            rotation, size, position = t["m_LocalRotation"], t["m_LocalScale"], t["m_LocalPosition"]
            if (any(rotation.get(axis, 0.0) != 0.0 for axis in _AXES) or rotation.get("w", 1.0) != 1.0
                    or any(size.get(axis, 1.0) != 1.0 for axis in _AXES)):
                raise _refuse(root)
            x, y = x + position["x"], y + position["y"]
            father = t["m_Father"]["m_PathID"]
        return x, y

    scale_ids = [target(pointer) for pointer in scaled]
    move_ids = list(scale_ids)
    for setting in settings:
        if not isinstance(setting, dict) or not all(_vector(setting.get(key)) for key in ("MostWideAdd", "MostNarrowAdd")):
            raise _refuse(root)
        move_ids.append(target(setting.get("Transform")))
        for key in ("MostWideAdd", "MostNarrowAdd"):
            setting[key] = {**setting[key], **{axis: setting[key][axis] * scale for axis in _AXES}}
    if top in move_ids:
        raise _refuse(root)
    out: dict[int, dict] = {component_id: component}
    for pid in dict.fromkeys(move_ids):
        t = copy.deepcopy(tree(pid))
        if not _vector(t.get("m_LocalPosition")) or not _vector(t.get("m_LocalScale")):
            raise _refuse(root)
        px, py = parents(pid)
        position = t["m_LocalPosition"]
        position["x"] = scale * (px + position["x"]) + ox - px
        position["y"] = scale * (py + position["y"]) + oy - py
        if pid in scale_ids:
            t["m_LocalScale"] = {**t["m_LocalScale"], **{axis: t["m_LocalScale"][axis] * scale for axis in _AXES}}
        out[pid] = t
    return out


def _f32(value: float) -> float:
    import struct
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _same(a: Any, b: Any) -> bool:
    """Typetrees equal, floats as the file keeps them (single precision)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return _number(a) and _number(b) and _f32(float(a)) == _f32(float(b))
    return a == b


def _prefab_objects(bundle: Bundle) -> dict[int, Any]:
    """The prefab file's objects by path id: a lobby prefab bundle holds one serialized file."""
    files = {obj.assets_file.name for obj in bundle.env.objects}
    if len(files) != 1:
        raise ServiceError("lobby_camera_unsupported", name=bundle.name)
    return {obj.path_id: obj for obj in bundle.env.objects}


def _streams(bundle: Bundle) -> dict[str, str]:
    return {name: hashlib.sha256(file.bytes).hexdigest() for name, file in bundle.env.file.files.items()
            if not hasattr(file, "objects")}


def read_trees(objects: dict[int, Any]) -> tuple[dict[int, str], Callable[[int], dict], dict[int, str]]:
    """kinds, a caching typetree reader and the MonoScript class names of a prefab file's objects."""
    kinds = {pid: obj.type.name for pid, obj in objects.items()}
    cache: dict[int, dict] = {}

    def tree(pid: int) -> dict:
        if pid not in cache:
            cache[pid] = objects[pid].read_typetree()
        return cache[pid]

    scripts = {pid: tree(pid).get("m_ClassName") for pid, kind in kinds.items() if kind == "MonoScript"}
    return kinds, tree, scripts


def patch_prefab(base: Path, output: Path, root: str, scale: float, offset: tuple[float, float],
                 *, compression: str = "lz4", crc: bool = True) -> None:
    """Write ``base`` with the camera to ``output`` (its name gives the CRC to fix to), then check that only the
    camera's objects changed, and to what they should be (``lobby_camera_failed`` otherwise)."""
    bundle = Bundle.load(base)
    if bundle is None:
        raise ServiceError("bundle_invalid", file=base)
    objects = _prefab_objects(bundle)
    kinds, tree, scripts = read_trees(objects)
    changed = camera_trees(kinds, tree, scripts, root, scale, offset)
    before = {pid: hashlib.sha256(obj.get_raw_data()).hexdigest() for pid, obj in objects.items() if pid not in changed}
    streams = _streams(bundle)
    for pid, new in changed.items():
        objects[pid].save_typetree(new)
    success, _ = bundle.save(output, SaveOptions(perform_crc=crc, compression=compression))
    if not success:
        raise ServiceError("lobby_camera_failed")
    checked = Bundle.load(output)
    if checked is None:
        raise ServiceError("lobby_camera_failed")
    after = _prefab_objects(checked)
    if (set(after) != set(objects) or _streams(checked) != streams
            or any(hashlib.sha256(after[pid].get_raw_data()).hexdigest() != digest for pid, digest in before.items())
            or any(not _same(after[pid].read_typetree(), new) for pid, new in changed.items())):
        raise ServiceError("lobby_camera_failed")


def compose(first: tuple[float, tuple[float, float]], then: tuple[float, tuple[float, float]]) -> tuple[float, list[float]]:
    """A camera framed on a lobby that already has one: ``p -> k2 (k1 p + o1) + o2``."""
    (k1, (x1, y1)), (k2, (x2, y2)) = first, then
    return k1 * k2, [k2 * x1 + x2, k2 * y1 + y2]
