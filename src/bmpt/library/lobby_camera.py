"""A memorial lobby with its own camera: the lobby prefab framed anew, kept as a Memorial Lobby choice.

The game scales a lobby by its screen's aspect ratio with the prefab root's ``MX.Utils.UIManualScaleByAspect``: it
multiplies its ``scaleTransforms``' local scale and adds whole units to its ``offsetSettings``' local positions
(``gameassets.lobby.aspect_rule``); nothing else moves those Transforms. A camera of scale ``k`` and offset ``o``
(NGUI units, ``lobby.js cameraParams``) is the similarity ``p -> k p + o`` about the screen centre: the scale targets
scaled by ``k`` and both kinds of target moved to ``k p + o`` (in the root's frame), the component's offsets times
``k``. It holds at every aspect, since the component multiplies the scale and adds the offsets. Only those objects of
the prefab bundle change; every other object and stream stays byte for byte.

A camera is a generated generic mod of the student's own prefab bundle. Prefabs are packed one per bundle,
so each slot is keyed by its CAB. Parameters live under TAG, while the App lists the record as a Memorial Lobby
choice (mod:<id>, kind lobby). Applying it puts its source art in the lobby slot and its prefab in the prefab
slot. Another lobby choice or a lobby restore returns the prefab slot to the camera's base.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import math
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bmpt.engine import Bundle, SaveOptions

from ..core import devmode
from ..core.errors import ServiceError
from ..core.logging_utils import log_failure
from ..core.openfiles import temporary_directory
from . import profiles
from .judge import core_of, other_target, packed_cab

if TYPE_CHECKING:
    from .models import ApplyResult, Pin
    from .service import ModLibrary

logger = logging.getLogger(__name__)

TAG = "lobby_camera"
SCRIPT = "UIManualScaleByAspect"
_TRANSFORM = "Transform"
_AXES = "xyz"
# the most a camera may scale a lobby by, either way, and move it by: far past what the framing limits allow
SCALE_RANGE = (0.1, 10.0)
OFFSET_LIMIT = 20000.0
# the most a lobby's timeline bundle weighs (the official ones up to 15 KB): larger lobby files are never opened
_TIMELINE_MAX = 1 << 20


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value: Any) -> bool:
    return isinstance(value, dict) and all(_number(value.get(axis)) for axis in _AXES)


def _refuse(root: str) -> ServiceError:
    return ServiceError("lobby_camera_unsupported", name=root)


def check_params(scale: Any, offset: Any) -> tuple[float, tuple[float, float]]:
    """Validate a camera's finite scale and offset within the supported bounds.

    Args:
        scale: Numeric zoom multiplier within SCALE_RANGE; booleans are rejected.
        offset: Two numeric NGUI-unit offsets, each within OFFSET_LIMIT of zero.

    Returns:
        Float scale and (horizontal, vertical) offset.

    Raises:
        ServiceError: A parameter is invalid (bad_params).
    """
    if not _number(scale) or not SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]:
        raise ServiceError("bad_params", key="scale")
    if (
        not isinstance(offset, (list, tuple))
        or len(offset) != 2
        or not all(_number(v) for v in offset)
        or any(abs(v) > OFFSET_LIMIT for v in offset)
    ):
        raise ServiceError("bad_params", key="offset")
    return float(scale), (float(offset[0]), float(offset[1]))


NODE = "LobbyCamera"  # the camera node's GameObject, between the root and the scale targets


@dataclass
class CameraEdit:
    """Describe changed and added lobby-prefab objects for a camera.

    ``trees`` holds the new typetrees by path id, of changed objects and of the
    ``added`` ones (path id -> class); ``node`` the camera node's Transform, ``animator`` the root's new Animator and
    ``director`` the intro's PlayableDirector (to give the intro track to), each None when not made or not found.
    """

    trees: dict[int, dict]
    added: dict[int, str] = field(default_factory=dict)
    node: int | None = None
    animator: int | None = None
    director: int | None = None


def camera_edit(
    kinds: dict[int, str],
    tree: Callable[[int], dict],
    scripts: dict[int, str],
    root: str,
    scale: float,
    offset: tuple[float, float],
    fresh: Callable[[], int] | None = None,
) -> CameraEdit:
    """Plan camera changes and additions within one lobby prefab.

    With ``fresh``, when the scale targets are the root's children and the root has no Animator of its own, a camera
    node (a GameObject and its Transform, ``NODE``) goes between the root and the targets, holding the camera, and an
    Animator goes on the root for the intro's camera track (``lobby_camera_intro``): the targets keep their transforms.
    Otherwise (the 5 lobbies whose root Animator moves things below the targets, whose clips name them by path) the
    targets themselves are scaled and moved, and the intro is framed by the camera too. Either way the dialogue anchors
    move to ``k p + o`` and the component's offsets are scaled by ``k``.

    A prefab without the component on its root, or whose component or targets are not as described above (a target
    that is no plain Transform of this file, a parent between it and the root that is scaled or turned), is refused
    (``lobby_camera_unsupported``): nothing is guessed.

    Args:
        kinds: Unity class names indexed by local object path ID.
        tree: Cached typetree reader for an object's local path ID.
        scripts: MonoScript class names indexed by script path ID.
        root: Case-insensitive lobby root GameObject name.
        scale: Camera zoom multiplier applied around the screen center.
        offset: Horizontal and vertical translation in NGUI units in the root's frame.
        fresh: Allocates unused path IDs for a camera node and Animator; None edits targets directly.

    Raises:
        ServiceError: Parameters or the prefab's transform/component structure are unsupported.
    """
    scale, (ox, oy) = check_params(scale, offset)
    top = next(
        (
            pid
            for pid, kind in kinds.items()
            if kind == _TRANSFORM
            and not tree(pid)["m_Father"]["m_PathID"]
            and tree(tree(pid)["m_GameObject"]["m_PathID"])["m_Name"].lower() == root.lower()
        ),
        None,
    )
    if top is None:
        raise _refuse(root)
    root_go = tree(top)["m_GameObject"]["m_PathID"]
    found = []
    for item in tree(root_go).get("m_Component", []):
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
    if (
        not _number(component.get("mostWideScale"))
        or not _number(component.get("mostNarrowScale"))
        or not isinstance(scaled, list)
        or not scaled
        or not isinstance(settings, list)
    ):
        raise _refuse(root)

    def target(pointer: Any) -> int:
        if not isinstance(pointer, dict) or pointer.get("m_FileID") or kinds.get(pointer.get("m_PathID")) != _TRANSFORM:
            raise _refuse(root)
        return pointer["m_PathID"]

    def parents(pid: int) -> tuple[float, float]:
        """Sum the translation of a target's parents up to the root.

        The root's own Transform is the frame, so it is not counted. A parent that scales or turns is a refusal.
        """
        x = y = 0.0
        seen = set()
        father = tree(pid)["m_Father"]["m_PathID"]
        while father != top:
            if father in seen or kinds.get(father) != _TRANSFORM:
                raise _refuse(root)
            seen.add(father)
            t = tree(father)
            rotation, size, position = t["m_LocalRotation"], t["m_LocalScale"], t["m_LocalPosition"]
            if (
                any(rotation.get(axis, 0.0) != 0.0 for axis in _AXES)
                or rotation.get("w", 1.0) != 1.0
                or any(size.get(axis, 1.0) != 1.0 for axis in _AXES)
            ):
                raise _refuse(root)
            x, y = x + position["x"], y + position["y"]
            father = t["m_Father"]["m_PathID"]
        return x, y

    scale_ids = list(dict.fromkeys(target(pointer) for pointer in scaled))
    move_ids = list(scale_ids)
    for setting in settings:
        if not isinstance(setting, dict) or not all(
            _vector(setting.get(key)) for key in ("MostWideAdd", "MostNarrowAdd")
        ):
            raise _refuse(root)
        move_ids.append(target(setting.get("Transform")))
        for key in ("MostWideAdd", "MostNarrowAdd"):
            setting[key] = {**setting[key], **{axis: setting[key][axis] * scale for axis in _AXES}}
    if top in move_ids:
        raise _refuse(root)
    root_animated = any(
        kinds.get(item["component"]["m_PathID"]) == "Animator" for item in tree(root_go).get("m_Component", [])
    )
    node = (
        fresh is not None
        and not root_animated
        and all(tree(pid)["m_Father"]["m_PathID"] == top for pid in scale_ids)
        and not set(scale_ids) & set(move_ids[len(scale_ids) :])
    )
    edit = CameraEdit({component_id: component})
    for pid in dict.fromkeys(move_ids):
        t = copy.deepcopy(tree(pid))
        if not _vector(t.get("m_LocalPosition")) or not _vector(t.get("m_LocalScale")):
            raise _refuse(root)
        if node and pid in scale_ids:
            continue  # under the camera node, as they are
        px, py = parents(pid)
        position = t["m_LocalPosition"]
        position["x"] = scale * (px + position["x"]) + ox - px
        position["y"] = scale * (py + position["y"]) + oy - py
        if pid in scale_ids:
            t["m_LocalScale"] = {**t["m_LocalScale"], **{axis: t["m_LocalScale"][axis] * scale for axis in _AXES}}
        edit.trees[pid] = t
    if not node:
        return edit
    assert fresh is not None
    first = tree(scale_ids[0])
    go_id, tf_id = fresh(), fresh()
    go = copy.deepcopy(tree(first["m_GameObject"]["m_PathID"]))
    go.update(m_Name=NODE, m_IsActive=1, m_Component=[{"component": {"m_FileID": 0, "m_PathID": tf_id}}])
    tf = copy.deepcopy(first)
    tf.update(
        m_GameObject={"m_FileID": 0, "m_PathID": go_id},
        m_Father={"m_FileID": 0, "m_PathID": top},
        m_Children=[{"m_FileID": 0, "m_PathID": pid} for pid in scale_ids],
        m_LocalPosition={"x": ox, "y": oy, "z": 0.0},
        m_LocalRotation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        m_LocalScale={"x": scale, "y": scale, "z": scale},
    )
    if "m_LocalEulerAnglesHint" in tf:
        tf["m_LocalEulerAnglesHint"] = {"x": 0.0, "y": 0.0, "z": 0.0}
    edit.trees.update({go_id: go, tf_id: tf})
    edit.added.update({go_id: "GameObject", tf_id: _TRANSFORM})
    edit.node = tf_id
    top_tree = copy.deepcopy(tree(top))
    children, placed = [], False
    for child in top_tree["m_Children"]:
        if child.get("m_PathID") in scale_ids:
            if not placed:
                children.append({"m_FileID": 0, "m_PathID": tf_id})
                placed = True
        else:
            children.append(child)
    top_tree["m_Children"] = children
    edit.trees[top] = top_tree
    for pid in scale_ids:
        t = copy.deepcopy(tree(pid))
        t["m_Father"] = {"m_FileID": 0, "m_PathID": tf_id}
        edit.trees[pid] = t
    directors = [pid for pid, kind in kinds.items() if kind == "PlayableDirector"]
    animators = [pid for pid, kind in kinds.items() if kind == "Animator"]
    if len(directors) == 1 and animators:
        animator = copy.deepcopy(tree(animators[0]))
        animator.update(
            m_GameObject={"m_FileID": 0, "m_PathID": root_go},
            m_Controller={"m_FileID": 0, "m_PathID": 0},
            m_Avatar={"m_FileID": 0, "m_PathID": 0},
            m_Enabled=1,
            m_CullingMode=0,
            m_ApplyRootMotion=0,
        )
        animator_id = fresh()
        root_tree = copy.deepcopy(tree(root_go))
        root_tree["m_Component"] = [*root_tree["m_Component"], {"component": {"m_FileID": 0, "m_PathID": animator_id}}]
        edit.trees.update({animator_id: animator, root_go: root_tree})
        edit.added[animator_id] = "Animator"
        edit.animator, edit.director = animator_id, directors[0]
    return edit


def camera_trees(
    kinds: dict[int, str],
    tree: Callable[[int], dict],
    scripts: dict[int, str],
    root: str,
    scale: float,
    offset: tuple[float, float],
) -> dict[int, dict]:
    """Frame a lobby by editing scale targets directly, without inserting a camera node.

    Args:
        kinds: Prefab path IDs mapped to Unity classes for direct-target editing.
        tree: Supplies the prefab typetree for a local path ID.
        scripts: Local script IDs mapped to classes for locating UIManualScaleByAspect.
        root: Lobby root's GameObject name, matched without regard to case.
        scale: Camera zoom multiplier around the screen center.
        offset: NGUI-unit x/y translation applied to targets relative to the prefab root.

    Returns:
        Changed UIManualScaleByAspect and Transform typetrees indexed by path ID.
    """
    return camera_edit(kinds, tree, scripts, root, scale, offset).trees


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
    return {
        name: hashlib.sha256(file.bytes).hexdigest()
        for name, file in bundle.env.file.files.items()
        if not hasattr(file, "objects")
    }


def read_trees(objects: dict[int, Any]) -> tuple[dict[int, str], Callable[[int], dict], dict[int, str]]:
    """Prepare class names, a cached typetree reader, and MonoScript names for prefab objects.

    Args:
        objects: Object readers from one prefab serialized file, indexed by local path ID.
    """
    kinds = {pid: obj.type.name for pid, obj in objects.items()}
    cache: dict[int, dict] = {}

    def tree(pid: int) -> dict:
        if pid not in cache:
            cache[pid] = objects[pid].read_typetree()
        return cache[pid]

    scripts = {pid: tree(pid).get("m_ClassName") for pid, kind in kinds.items() if kind == "MonoScript"}
    return kinds, tree, scripts


def timeline_cab(prefab: Path) -> str | None:
    """Find a lobby prefab's external intro-timeline CAB in lower case, or None if unavailable."""
    bundle = Bundle.load(prefab)
    if bundle is None:
        return None
    objects = _prefab_objects(bundle)
    directors = [obj for obj in objects.values() if obj.type.name == "PlayableDirector"]
    if len(directors) != 1:
        return None
    asset = directors[0].read_typetree().get("m_PlayableAsset") or {}
    sf = directors[0].assets_file
    if asset.get("m_FileID", 0) <= 0 or asset["m_FileID"] > len(sf.externals):
        return None
    return sf.externals[asset["m_FileID"] - 1].path.rsplit("/", 1)[-1].lower()


def _fresh(sf: Any, seed: str) -> Callable[[], int]:
    rng = random.Random(zlib.crc32(seed.encode("utf-8")))
    taken: set[int] = set()

    def fresh() -> int:
        while True:
            pid = rng.randrange(-(1 << 63), 1 << 63)
            if pid and pid not in sf.objects and pid not in taken:
                taken.add(pid)
                return pid

    return fresh


def _add_like(sf: Any, kind: str, tree: dict, pid: int) -> None:
    """A new object of a class the file already holds (GameObject, Transform, Animator), at ``pid``."""
    template = next(obj for obj in sf.objects.values() if obj.type.name == kind)
    new = object.__new__(type(template))
    new.__dict__.update(template.__dict__)
    new.path_id = pid
    new.data = b""
    new.save_typetree(tree)
    sf.objects[pid] = new


def _preload(objects: dict[int, Any], added: list[int]) -> int | None:
    """The new objects in the prefab's preload range (``model_adapt._extend_preload``): the AssetBundle's path id."""
    from ..gameassets.model_adapt import _extend_preload

    for pid, obj in objects.items():
        if obj.type.name == "AssetBundle":
            tree = obj.read_typetree()
            _extend_preload(
                tree,
                {
                    entry: [{"m_FileID": 0, "m_PathID": p} for p in added]
                    for entry, (_, info) in enumerate(tree["m_Container"])
                    if info["preloadSize"]
                },
            )
            obj.save_typetree(tree)
            return pid
    return None


def patch_prefab(
    base: Path,
    output: Path,
    root: str,
    scale: float,
    offset: tuple[float, float],
    *,
    timeline: Path | None = None,
    compression: str = "lz4",
    crc: bool = True,
) -> bool:
    """Write a camera prefab and verify that only its intended objects changed or were added.

    Args:
        base: Prefab bundle supplying untouched objects and streams.
        output: Saved bundle path whose filename supplies the CRC to fix to.
        root: Lobby root GameObject name to frame.
        scale: Similarity-transform multiplier for the prefab's camera framing.
        offset: Horizontal and vertical translation in NGUI units.
        timeline: Art bundle holding the intro timeline; None leaves the intro in camera framing.
        compression: Engine bundle compression mode for the output.
        crc: Whether to fix the saved bytes to the output filename's CRC.

    Returns:
        Whether an added intro track preserves official framing until the rest pose.

    Raises:
        ServiceError: The bundle or camera structure is unsupported, saving fails,
            or post-save preservation checks fail (lobby_camera_failed).
    """
    from . import lobby_camera_intro as intro

    bundle = Bundle.load(base)
    if bundle is None:
        raise ServiceError("bundle_invalid", file=base)
    objects = _prefab_objects(bundle)
    sf = next(iter(objects.values())).assets_file
    kinds, tree, scripts = read_trees(objects)
    fresh = _fresh(sf, f"{base.name}:{root}")
    edit = camera_edit(kinds, tree, scripts, root, scale, offset, fresh)
    before = {pid: hashlib.sha256(obj.get_raw_data()).hexdigest() for pid, obj in objects.items()}
    streams = _streams(bundle)
    for pid in edit.added:
        _add_like(sf, edit.added[pid], edit.trees[pid], pid)
    for pid, new in edit.trees.items():
        if pid not in edit.added:
            objects[pid].save_typetree(new)
    added = list(edit.added)
    held = False
    if timeline is not None and edit.director is not None:
        held = _intro(intro, sf, edit, tree, timeline, fresh, scale, offset, added)
    ordered = sorted(sf.objects.items())
    sf.objects.clear()
    sf.objects.update(ordered)
    preload = _preload(sf.objects, added) if added else None
    expected = {pid: t for pid, t in edit.trees.items()}
    success, _ = bundle.save(output, SaveOptions(perform_crc=crc, compression=compression))
    if not success:
        raise ServiceError("lobby_camera_failed")
    checked = Bundle.load(output)
    if checked is None:
        raise ServiceError("lobby_camera_failed")
    after = _prefab_objects(checked)
    unchanged = {pid: digest for pid, digest in before.items() if pid not in expected and pid != preload}
    if (
        set(after) != set(before) | set(added)
        or _streams(checked) != streams
        or any(hashlib.sha256(after[pid].get_raw_data()).hexdigest() != digest for pid, digest in unchanged.items())
        or any(not _same(after[pid].read_typetree(), new) for pid, new in expected.items())
    ):
        raise ServiceError("lobby_camera_failed")
    return held


def _intro(
    intro: Any,
    sf: Any,
    edit: CameraEdit,
    tree: Callable[[int], dict],
    timeline: Path,
    fresh: Callable[[], int],
    scale: float,
    offset: tuple[float, float],
    added: list[int],
) -> bool:
    """Give the prefab its own intro timeline with the camera track (``lobby_camera_intro``).

    Returns:
        False, with nothing added, when the timeline file does not hold the director's timeline or the timeline has
        no rest pose to switch at; True otherwise.
    """
    director = copy.deepcopy(edit.trees.get(edit.director) or tree(edit.director))
    asset = director.get("m_PlayableAsset") or {}
    file_id = asset.get("m_FileID", 0)
    if file_id <= 0 or file_id > len(sf.externals):
        return False
    cab = sf.externals[file_id - 1].path.rsplit("/", 1)[-1].lower()
    other = Bundle.load(timeline)
    if other is None:
        return False
    found = {obj.path_id: obj for obj in other.env.objects if obj.assets_file.name.lower() == cab}
    if asset["m_PathID"] not in found:
        return False
    source = found[asset["m_PathID"]].assets_file
    try:
        writer = intro.Writer(sf, source, file_id, fresh)
    except ValueError:
        return False
    data = intro.Timeline(found)
    if data.cls(asset["m_PathID"]) != "TimelineAsset":
        return False
    (ox, oy) = offset
    made = intro.add_intro_track(
        writer, data, asset["m_PathID"], NODE, ((0.0, 0.0, 0.0, 1.0, 1.0, 1.0), (ox, oy, 0.0, scale, scale, scale))
    )
    if made is None:
        return False
    asset_id, track_id, new = made
    director["m_PlayableAsset"] = {"m_FileID": 0, "m_PathID": asset_id}
    director["m_SceneBindings"] = [
        *director["m_SceneBindings"],
        {"key": {"m_FileID": 0, "m_PathID": track_id}, "value": {"m_FileID": 0, "m_PathID": edit.animator}},
    ]
    sf.objects[edit.director].save_typetree(director)
    edit.trees[edit.director] = director
    for pid in new:
        edit.trees[pid] = writer.trees[pid]
    added.extend(new)
    return True


def compose(
    first: tuple[float, tuple[float, float]], then: tuple[float, tuple[float, float]]
) -> tuple[float, list[float]]:
    """Compose camera transforms as p -> k2 (k1 p + o1) + o2.

    Args:
        first: Existing camera's scale and horizontal/vertical NGUI offset.
        then: New framing's scale and horizontal/vertical offset applied to the existing camera.
    """
    (k1, (x1, y1)), (k2, (x2, y2)) = first, then
    return k1 * k2, [k2 * x1 + x2, k2 * y1 + y2]


def meta_of(record: dict | None) -> dict | None:
    """Return a generic record's camera parameters, or None for absent or noncamera records."""
    meta = (record or {}).get(TAG) if (record or {}).get("kind") == "other" else None
    return meta if isinstance(meta, dict) and isinstance(meta.get("variant"), str) else None


def cameras(library: ModLibrary, variant: str | None = None) -> list[dict]:
    """List stored camera records, optionally restricted to one variant.

    Args:
        library: Library whose managed generic records may contain camera metadata.
        variant: Variant key, or None to include cameras for every variant.
    """
    return [
        record
        for record in library._records.values()
        if (meta := meta_of(record)) is not None and (variant is None or meta["variant"] == variant)
    ]


def camera_of(library: ModLibrary, source: Any) -> dict | None:
    """Resolve a library source to a camera record.

    Args:
        library: Library holding the source records.
        source: mod:<id> reference; other values or noncamera records return None.
    """
    if not isinstance(source, str) or not source.startswith("mod:"):
        return None
    record = library._records.get(source[4:])
    return record if meta_of(record) is not None else None


def _live(library: ModLibrary, name: str, pin: Pin) -> Path:
    found = [Path(path) for entry, path in library._listing(pin) if entry == name]
    if len(found) != 1:
        raise ServiceError("game_file_not_found", variant=name)
    return found[0]


def _slot(library: ModLibrary, live: Path, pin: Pin) -> str:
    """The prefab's own generic slot: keyed by its CAB, never the slot every lobby prefab would share by name."""
    library._note_packed([live], pin)
    if packed_cab(live.name) is None:
        raise ServiceError("lobby_camera_unsupported", name=live.name)
    return core_of(live.name)


def _configured(library: ModLibrary, pin: Pin) -> dict:
    return (((library.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}


def timeline_file(library: ModLibrary, variant: str, source: str, prefab: Path, pin: Pin) -> Path | None:
    """Find the intro timeline matching a camera's art source.

    Prefer the lobby mod's own matching file, falling back to the verified official
    copy. Return None when no lobby file holds the prefab's external timeline CAB.

    Args:
        library: Library providing source records, lobby files, and official copies.
        variant: Variant key whose lobby art is searched.
        source: official or a lobby mod:<id> whose own timeline takes precedence.
        prefab: Camera base prefab whose PlayableDirector identifies the timeline CAB.
        pin: Game install and server the operation is fixed to.
    """
    cab = timeline_cab(prefab)
    if cab is None:
        return None
    record = library._records.get(source[4:]) if source.startswith("mod:") else None
    own = {path.name: path for path in library._paths(record)} if record is not None else {}
    for live in library._game_files(variant, "lobby", pin):
        candidate = own.get(live.name)
        if candidate is None:
            candidate = library._official_for(live, pin, full=False)
        if candidate.stat().st_size > _TIMELINE_MAX:
            continue
        bundle = Bundle.load(candidate)
        if bundle is not None and any(name.lower() == cab for name in bundle.env.file.files):
            return candidate
    return None


def save(
    library: ModLibrary, variant: str, source: str, scale: float, offset: Any, prefab: str, root: str, pin: Pin, log
) -> dict:
    """Store a new memorial-lobby camera choice, composing any existing camera framing.

    Args:
        library: Library storing the generated prefab and its source-art recipe.
        variant: Variant key whose memorial lobby is framed.
        source: official, a lobby mod:<id>, or a camera mod:<id> to compose with the new framing.
        scale: Additional camera zoom multiplier, validated after composition too.
        offset: Horizontal and vertical NGUI-unit offset for the additional framing.
        prefab: Live ui-uilobbyelement bundle basename containing the lobby prefab.
        root: Prefab root GameObject name.
        pin: Game install and server the operation is fixed to.
        log: Receives user-facing operation messages.

    Returns:
        Stored generic record with camera metadata, number, and base source.
    """
    k, (ox, oy) = check_params(scale, offset)
    v = library._variant(variant)
    if not v.lobby:
        raise ServiceError("game_file_not_found", variant=variant)
    core = v.lobby.lower()
    earlier = camera_of(library, source)
    if earlier is not None:
        meta = meta_of(earlier)
        if meta["variant"] != variant:
            raise ServiceError("mod_target_mismatch", id=earlier["id"], variant=variant)
        k, (ox, oy) = compose((meta["scale"], tuple(meta["offset"])), (k, (ox, oy)))
        k, (ox, oy) = check_params(k, (ox, oy))
        source = meta["source"]
    if source != "official":
        record = library._record(source[4:]) if source.startswith("mod:") else None
        if (
            record is None
            or record["kind"] != "lobby"
            or library._target_of(record) != variant
            or core_of(record["files"][0]) != core
        ):
            raise ServiceError("source_unsupported", source=source)
    if not prefab.startswith("ui-uilobbyelement-") or Path(prefab).name != prefab:
        raise ServiceError("lobby_camera_unsupported", name=prefab)
    live = _live(library, prefab, pin)
    slot = _slot(library, live, pin)
    configured = (_configured(library, pin).get(slot) or {}).get("source", "")
    base_record = library._records.get(configured[4:]) if configured.startswith("mod:") else None
    base_source = configured if base_record is not None and meta_of(base_record) is None else "official"
    with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
        work = Path(temporary)
        if base_record is not None and base_source != "official":
            base = library._install_direct(base_record, [live], work / "base", log, None, pin)[live]
        else:
            base = library._official_for(live, pin)
        (work / "camera").mkdir()
        output = work / "camera" / live.name
        timeline = timeline_file(library, variant, source, base, pin)
        held = patch_prefab(base, output, root, k, (ox, oy), timeline=timeline, crc=library.settings.crc_needed(live))
        number = 1 + max((meta_of(r).get("number", 0) for r in cameras(library, variant)), default=0)
        meta = {
            "variant": variant,
            "core": core,
            "source": source,
            "scale": k,
            "offset": [ox, oy],
            "root": root,
            "prefab": live.name,
            "slot": slot,
            "base": base_source,
            "number": number,
            "timeline": timeline.name if held and timeline is not None else None,
        }
        files = {live.name: output}
        return library._store(files, "Lobby camera", other_target(files), "other", metadata={TAG: meta})


def apply(library: ModLibrary, variant: str, record: dict, log, progress, pin: Pin, create_backup: bool) -> ApplyResult:
    """Apply a camera's lobby art and generic prefab, deferring both when mods are off.

    Args:
        library: Library applying the camera's two coordinated source choices.
        variant: Variant key that must match the camera record.
        record: Stored camera record identifying art, prefab, and any required refresh.
        log: Receives user-facing operation messages.
        progress: Receives apply progress; None disables reporting.
        pin: Game install and server the operation is fixed to.
        create_backup: Whether applying the art and prefab also keeps adjacent .backup files.

    Returns:
        Combined art and prefab result retaining incomplete and kept-current art status.
    """
    from .models import ApplyResult

    meta = meta_of(record)
    if meta["variant"] != variant:
        raise ServiceError("mod_target_mismatch", id=record["id"], variant=variant)
    refresh(library, [pin], log, only={record["id"]})
    record = library._record(record["id"])
    art = library._apply_choice(variant, meta["source"], log, progress, "lobby", pin, create_backup=create_backup)
    prefab = library._apply_other(record["target"], f"mod:{record['id']}", log, progress, pin, create_backup)
    return ApplyResult(
        variant,
        f"mod:{record['id']}",
        [*art.files, *prefab.files],
        art.unchanged and prefab.unchanged,
        deferred=art.deferred or prefab.deferred,
        kept_current=art.kept_current or prefab.kept_current,
        incomplete=art.incomplete,
    )


def release(library: ModLibrary, variant: str, pin: Pin, log, progress) -> None:
    """Return camera prefab slots to their bases when another lobby choice or restore replaces the art.

    Args:
        library: Library applying the official prefab or generic prefab mod used as each camera's base.
        variant: Variant key whose configured camera slots must be released.
        pin: Game install and server the operation is fixed to.
        log: Receives user-facing operation messages.
        progress: Receives apply progress; None disables reporting.
    """
    for slot, entry in list(_configured(library, pin).items()):
        record = camera_of(library, entry.get("source"))
        meta = meta_of(record)
        if meta is None or meta["variant"] != variant:
            continue
        base = meta.get("base", "official")
        base_record = library._records.get(base[4:]) if isinstance(base, str) and base.startswith("mod:") else None
        if base_record is not None and base_record.get("kind") == "other":
            # the prefab mod the camera was framed on, put back: no untested build's confirmation (tested_build)
            library._apply_other(base_record["target"], base, log, progress, pin, False, untested=False)
        else:
            library.apply(slot, "official", log=log, progress=progress, kind="other", pin=pin)


def stale(library: ModLibrary, record: dict, pin: Pin) -> Path | None:
    """Find the live prefab when a camera's prefab or copied intro timeline is outdated.

    Args:
        library: Library providing the live generic slot and lobby-art filenames.
        record: Stored camera record with its base prefab and optional copied-timeline name.
        pin: Game install and server the operation is fixed to.

    Returns:
        The sole live prefab if either baseline changed, or None otherwise or when the slot is unavailable.
    """
    meta = meta_of(record)
    paths = library._index(pin).get(meta["slot"], [])
    if len(paths) != 1:
        return None
    if paths[0].name != record["files"][0]:
        return paths[0]
    timeline = meta.get("timeline")
    if timeline and timeline not in {path.name for path in library._game_files(meta["variant"], "lobby", pin)}:
        return paths[0]
    return None


def refresh(library: ModLibrary, pins: list[Pin], log, only: set[str] | None = None) -> None:
    """Rebuild official-base cameras in place on updated prefabs with their existing framing.

    Retain the choice, alias, number, scale, and offset. A camera that cannot be made again keeps its
    file; applying it updates that file onto the new prefab like any generic mod.

    Args:
        library: Library retaining camera records and resolving official prefab copies.
        pins: Game installs and servers the operation is fixed to.
        log: Receives user-facing operation messages.
        only: Camera IDs to refresh; None considers every stored camera.
    """
    from .storage import atomic_copy, verify_file

    for pin in pins:
        for record in cameras(library):
            meta = meta_of(record)
            if (only is not None and record["id"] not in only) or meta.get("base") != "official":
                continue
            try:
                live = stale(library, record, pin)
                if live is None:
                    continue
                with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
                    output = Path(temporary) / live.name
                    base = library._official_for(live, pin)
                    timeline = timeline_file(library, meta["variant"], meta["source"], base, pin)
                    held = patch_prefab(
                        base,
                        output,
                        meta["root"],
                        meta["scale"],
                        tuple(meta["offset"]),
                        timeline=timeline,
                        crc=library.settings.crc_needed(live),
                    )
                    old = library._paths(record)[0]
                    target = old.parent / live.name
                    digest = library._hashes.get(output).sha1
                    atomic_copy(output, target)
                    verify_file(target, digest)
                record["files"] = [live.name]
                meta["prefab"] = live.name
                meta["timeline"] = timeline.name if held and timeline is not None else None
                library._save()
                if old != target:
                    old.unlink(missing_ok=True)
                log(f"Lobby camera made again on the updated prefab: {live.name}")
            except (ServiceError, OSError) as error:
                log_failure(logger, logging.WARNING, "Lobby camera kept on its earlier prefab", error)


def preview_files(library: ModLibrary, record: dict, pin: Pin) -> tuple[Path, Path | None, Path | None]:
    """Choose the stored camera prefab and any updated official inputs needed for its preview.

    Args:
        library: Library providing stored files, official copies, and source-art timelines.
        record: Camera record whose prefab and copied timeline may be outdated.
        pin: Game install and server the operation is fixed to.

    Returns:
        Stored prefab, optional official prefab to frame on the fly, and optional art timeline.
    """
    live = stale(library, record, pin)
    if live is None:
        return library._paths(record)[0], None, None
    meta = meta_of(record)
    official = library._official_for(live, pin, full=False)
    try:
        timeline = timeline_file(library, meta["variant"], meta["source"], official, pin)
    except ServiceError:
        timeline = None
    return library._paths(record)[0], official, timeline


def live_base(library: ModLibrary, core: str, pin: Pin) -> Path | None:
    """Supply unframed official prefab bytes for other choices when a lobby camera is installed.

    Args:
        library: Library inspecting carried prefab sources and locating official copies.
        core: Lobby bundle core whose preview must avoid the installed camera's framing.
        pin: Game install and server the operation is fixed to.

    Returns:
        Official prefab path, or None when no camera is carried by the core's prefab slot.
    """
    view = None
    for record in cameras(library):
        meta = meta_of(record)
        if meta["core"] != core.lower():
            continue
        for path in library._index(pin).get(meta["slot"], []):
            view = view or library._view([pin], "family")
            carried = view.part(path, pin, "other").carried
            if camera_of(library, carried) is not None:
                return library._official_for(path, pin, full=False)
    return None


def _chosen(records: list[dict], folder: dict, lobby: str | None) -> dict | None:
    """The camera of a variant its prefab slot holds, when its lobby slot holds the camera's source."""
    for record in records:
        meta = meta_of(record)
        entry = folder.get(meta["slot"]) or {}
        if entry.get("source") == f"mod:{record['id']}" and (lobby or "official") == meta["source"]:
            return record
    return None


def overlay(records: list[dict], folder: dict, sources: dict) -> dict:
    """Overlay a coordinated camera choice onto a variant's configured lobby source.

    Args:
        records: Camera records belonging to the variant being described.
        folder: Install's configured entries indexed by slot core.
        sources: Variant's configured sources by kind, returned unchanged if no camera matches.
    """
    record = _chosen(records, folder, sources.get("lobby"))
    if record is not None:
        sources = {**sources, "lobby": f"mod:{record['id']}"}
    return sources


def overlay_selection(
    library: ModLibrary, variant: str, pin: Pin, config: dict | None, folder: dict, result: dict
) -> dict:
    """Overlay a saved camera choice, marking it installed only when its art and prefab are installed.

    Args:
        library: Library providing camera records and prefab-selection state.
        variant: Variant key whose saved choices are described.
        pin: Game install and server the operation is fixed to.
        config: Profile snapshot, or None when no configuration exists.
        folder: Pinned install's slot choices, including the camera prefab entry to verify.
        result: Variant's selection by kind; its lobby entry is replaced in place when a camera matches.
    """
    records = cameras(library, variant)
    art = result.get("lobby")
    record = _chosen(records, folder, (art or {}).get("source")) if records else None
    if record is None:
        return result
    meta = meta_of(record)
    prefab = library._selection_of(pin, config, meta["slot"], folder[meta["slot"]])
    installed = prefab["installed"] and (art["installed"] if art else profiles.enabled(config))
    error = None if installed else ((art or {}).get("error") or prefab.get("error"))
    result["lobby"] = {
        "source": f"mod:{record['id']}",
        "installed": installed,
        "error": error,
        **({"incomplete": True} if (art or {}).get("incomplete") else {}),
    }
    return result
