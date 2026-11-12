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
import logging
import math
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bmpt.engine import Bundle, SaveOptions

from .. import devmode
from ..errors import ServiceError
from ..logging_utils import log_failure
from ..openfiles import temporary_directory
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
    """A camera's scale and offset as numbers, or ``bad_params``."""
    if not _number(scale) or not SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]:
        raise ServiceError("bad_params", key="scale")
    if (not isinstance(offset, (list, tuple)) or len(offset) != 2 or not all(_number(v) for v in offset)
            or any(abs(v) > OFFSET_LIMIT for v in offset)):
        raise ServiceError("bad_params", key="offset")
    return float(scale), (float(offset[0]), float(offset[1]))


NODE = "LobbyCamera"  # the camera node's GameObject, between the root and the scale targets (BL-187)


@dataclass
class CameraEdit:
    """What a camera changes in a lobby prefab: ``trees`` the new typetrees by path id, of changed objects and of the
    ``added`` ones (path id -> class); ``node`` the camera node's Transform, ``animator`` the root's new Animator and
    ``director`` the intro's PlayableDirector (to give the intro track to), each None when not made or not found."""
    trees: dict[int, dict]
    added: dict[int, str] = field(default_factory=dict)
    node: int | None = None
    animator: int | None = None
    director: int | None = None


def camera_edit(kinds: dict[int, str], tree: Callable[[int], dict], scripts: dict[int, str], root: str,
                scale: float, offset: tuple[float, float], fresh: Callable[[], int] | None = None) -> CameraEdit:
    """The objects a camera changes or adds in one lobby prefab. kinds, tree and scripts as ``lobby.read_prefab``
    takes them; ``fresh`` gives path ids for new objects.

    With ``fresh``, when the scale targets are the root's children and the root has no Animator of its own, a camera
    node (a GameObject and its Transform, ``NODE``) goes between the root and the targets, holding the camera, and an
    Animator goes on the root for the intro's camera track (``lobby_camera_intro``): the targets keep their transforms.
    Otherwise (the 5 lobbies whose root Animator moves things below the targets, whose clips name them by path) the
    targets themselves are scaled and moved, and the intro is framed by the camera too. Either way the dialogue anchors
    move to ``k p + o`` and the component's offsets are scaled by ``k``.

    A prefab without the component on its root, or whose component or targets are not as described above (a target
    that is no plain Transform of this file, a parent between it and the root that is scaled or turned), is refused
    (``lobby_camera_unsupported``): nothing is guessed."""
    scale, (ox, oy) = check_params(scale, offset)
    top = next((pid for pid, kind in kinds.items() if kind == _TRANSFORM and not tree(pid)["m_Father"]["m_PathID"]
                and tree(tree(pid)["m_GameObject"]["m_PathID"])["m_Name"].lower() == root.lower()), None)
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

    scale_ids = list(dict.fromkeys(target(pointer) for pointer in scaled))
    move_ids = list(scale_ids)
    for setting in settings:
        if not isinstance(setting, dict) or not all(_vector(setting.get(key)) for key in ("MostWideAdd", "MostNarrowAdd")):
            raise _refuse(root)
        move_ids.append(target(setting.get("Transform")))
        for key in ("MostWideAdd", "MostNarrowAdd"):
            setting[key] = {**setting[key], **{axis: setting[key][axis] * scale for axis in _AXES}}
    if top in move_ids:
        raise _refuse(root)
    root_animated = any(kinds.get(item["component"]["m_PathID"]) == "Animator"
                        for item in tree(root_go).get("m_Component", []))
    node = (fresh is not None and not root_animated
            and all(tree(pid)["m_Father"]["m_PathID"] == top for pid in scale_ids)
            and not set(scale_ids) & set(move_ids[len(scale_ids):]))
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
    tf.update(m_GameObject={"m_FileID": 0, "m_PathID": go_id}, m_Father={"m_FileID": 0, "m_PathID": top},
              m_Children=[{"m_FileID": 0, "m_PathID": pid} for pid in scale_ids],
              m_LocalPosition={"x": ox, "y": oy, "z": 0.0},
              m_LocalRotation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
              m_LocalScale={"x": scale, "y": scale, "z": scale})
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
        animator.update(m_GameObject={"m_FileID": 0, "m_PathID": root_go}, m_Controller={"m_FileID": 0, "m_PathID": 0},
                        m_Avatar={"m_FileID": 0, "m_PathID": 0}, m_Enabled=1, m_CullingMode=0, m_ApplyRootMotion=0)
        animator_id = fresh()
        root_tree = copy.deepcopy(tree(root_go))
        root_tree["m_Component"] = [*root_tree["m_Component"], {"component": {"m_FileID": 0, "m_PathID": animator_id}}]
        edit.trees.update({animator_id: animator, root_go: root_tree})
        edit.added[animator_id] = "Animator"
        edit.animator, edit.director = animator_id, directors[0]
    return edit


def camera_trees(kinds: dict[int, str], tree: Callable[[int], dict], scripts: dict[int, str], root: str,
                 scale: float, offset: tuple[float, float]) -> dict[int, dict]:
    """The new typetrees of a camera that scales and moves the scale targets themselves (``camera_edit`` without a
    camera node): the root's UIManualScaleByAspect and its target Transforms, by path id."""
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


def timeline_cab(prefab: Path) -> str | None:
    """The CAB name of the file holding a lobby prefab's intro timeline (its PlayableDirector's asset), lower case;
    None for a prefab without one in another file."""
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
            _extend_preload(tree, {(info["preloadIndex"], info["preloadSize"]): [{"m_FileID": 0, "m_PathID": p} for p in added]
                                   for _, info in tree["m_Container"] if info["preloadSize"]})
            obj.save_typetree(tree)
            return pid
    return None


def patch_prefab(base: Path, output: Path, root: str, scale: float, offset: tuple[float, float],
                 *, timeline: Path | None = None, compression: str = "lz4", crc: bool = True) -> bool:
    """Write ``base`` with the camera to ``output`` (its name gives the CRC to fix to), then check that only the
    camera's objects changed or were added, and to what they should be (``lobby_camera_failed`` otherwise).
    ``timeline``: the bundle holding the lobby's intro timeline (``timeline_cab``), as the camera's art has it; with it
    the intro keeps the official framing (``lobby_camera_intro``). Returns whether it does."""
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
    if (set(after) != set(before) | set(added) or _streams(checked) != streams
            or any(hashlib.sha256(after[pid].get_raw_data()).hexdigest() != digest for pid, digest in unchanged.items())
            or any(not _same(after[pid].read_typetree(), new) for pid, new in expected.items())):
        raise ServiceError("lobby_camera_failed")
    return held


def _intro(intro: Any, sf: Any, edit: CameraEdit, tree: Callable[[int], dict], timeline: Path,
           fresh: Callable[[], int], scale: float, offset: tuple[float, float], added: list[int]) -> bool:
    """Give the prefab its own intro timeline with the camera track (``lobby_camera_intro``); False, and nothing
    added, when the timeline file does not hold the director's timeline or it has no rest pose to switch at."""
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
    made = intro.add_intro_track(writer, data, asset["m_PathID"], NODE,
                                 ((0.0, 0.0, 0.0, 1.0, 1.0, 1.0), (ox, oy, 0.0, scale, scale, scale)))
    if made is None:
        return False
    asset_id, track_id, new = made
    director["m_PlayableAsset"] = {"m_FileID": 0, "m_PathID": asset_id}
    director["m_SceneBindings"] = [*director["m_SceneBindings"],
                                   {"key": {"m_FileID": 0, "m_PathID": track_id},
                                    "value": {"m_FileID": 0, "m_PathID": edit.animator}}]
    sf.objects[edit.director].save_typetree(director)
    edit.trees[edit.director] = director
    for pid in new:
        edit.trees[pid] = writer.trees[pid]
    added.extend(new)
    return True


def compose(first: tuple[float, tuple[float, float]], then: tuple[float, tuple[float, float]]) -> tuple[float, list[float]]:
    """A camera framed on a lobby that already has one: ``p -> k2 (k1 p + o1) + o2``."""
    (k1, (x1, y1)), (k2, (x2, y2)) = first, then
    return k1 * k2, [k2 * x1 + x2, k2 * y1 + y2]


# --- the library's lobby camera choices ---
# A camera is kept as a generated generic mod of the student's own prefab bundle (one prefab per bundle: a group packed
# one asset per bundle, so the library gives each its own slot keyed by its CAB), with its parameters under TAG. The
# App lists it as a Memorial Lobby choice of its variant (``mod:<id>``, kind "lobby"): applying it puts the art of its
# source (official, or the lobby mod it was framed on) in the lobby slot and the camera's prefab in the prefab slot.
# Any other lobby choice of the variant, and a restore of its lobby, put the prefab slot back to the camera's base.


def meta_of(record: dict | None) -> dict | None:
    """A record's camera parameters, None for any other record."""
    meta = (record or {}).get(TAG) if (record or {}).get("kind") == "other" else None
    return meta if isinstance(meta, dict) and isinstance(meta.get("variant"), str) else None


def cameras(library: ModLibrary, variant: str | None = None) -> list[dict]:
    return [record for record in library._records.values()
            if (meta := meta_of(record)) is not None and (variant is None or meta["variant"] == variant)]


def camera_of(library: ModLibrary, source: Any) -> dict | None:
    """The camera record a ``mod:<id>`` source names, else None."""
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
    """The bundle holding a lobby's intro timeline as the camera's art has it: the lobby mod's own file of that name
    when ``source`` is a lobby mod that holds one, else the official copy of the game's. None when the prefab's intro
    is in no lobby file of the variant."""
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


def save(library: ModLibrary, variant: str, source: str, scale: float, offset: Any, prefab: str, root: str,
         pin: Pin, log) -> dict:
    """Keep a new camera choice of a variant's memorial lobby: ``source`` is what the lobby showed ("official",
    ``mod:<id>`` of one of its lobby mods, or one of its cameras, composed), ``prefab`` / ``root`` its prefab bundle's
    file name and root GameObject. Returns the stored record."""
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
        if (record is None or record["kind"] != "lobby" or library._target_of(record) != variant
                or core_of(record["files"][0]) != core):
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
        meta = {"variant": variant, "core": core, "source": source, "scale": k, "offset": [ox, oy], "root": root,
                "prefab": live.name, "slot": slot, "base": base_source, "number": number,
                "timeline": timeline.name if held and timeline is not None else None}
        files = {live.name: output}
        return library._store(files, "Lobby camera", other_target(files), "other", metadata={TAG: meta})


def apply(library: ModLibrary, variant: str, record: dict, log, progress, pin: Pin, create_backup: bool) -> ApplyResult:
    """Put a camera choice in the game: its source's art through the lobby's own apply, then its prefab through the
    generic one (both only configured while mods are off)."""
    from .models import ApplyResult

    meta = meta_of(record)
    if meta["variant"] != variant:
        raise ServiceError("mod_target_mismatch", id=record["id"], variant=variant)
    refresh(library, [pin], log, only={record["id"]})
    record = library._record(record["id"])
    art = library._apply_choice(variant, meta["source"], log, progress, "lobby", pin, create_backup=create_backup)
    prefab = library._apply_other(record["target"], f"mod:{record['id']}", log, progress, pin, create_backup)
    # the art's own result: incomplete or kept as it is
    return ApplyResult(variant, f"mod:{record['id']}", [*art.files, *prefab.files], art.unchanged and prefab.unchanged,
                       deferred=art.deferred or prefab.deferred, kept_current=art.kept_current or prefab.kept_current,
                       incomplete=art.incomplete)


def release(library: ModLibrary, variant: str, pin: Pin, log, progress) -> None:
    """After another lobby choice of a variant (or a restore of its lobby): a prefab slot holding one of its cameras
    goes back to that camera's base, the official prefab or the generic prefab mod it was framed on."""
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
    """The game's prefab file when a game update replaced the one a camera was made on, or the intro timeline bundle
    its own timeline copies (``meta["timeline"]``), else None."""
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
    """Make each camera framed on the official prefab anew on the game's new prefab after an update, with the same
    scale and offset, in place (the same choice, alias and number). A camera that cannot be made again keeps its
    file; applying it updates that file onto the new prefab like any generic mod."""
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
                    held = patch_prefab(base, output, meta["root"], meta["scale"], tuple(meta["offset"]),
                                        timeline=timeline, crc=library.settings.crc_needed(live))
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
    """What a camera's preview reads: its own prefab file, or, after a game update it was not made again for yet, the
    game's official prefab to frame on the fly (second item) with the intro timeline bundle of its art (third)."""
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
    """For the lobby of a core whose prefab in the game carries one of its cameras: the official prefab, which the
    other choices' previews read (they never show a camera's framing); else None."""
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
    """A variant's configured sources (``ModLibrary.configured``) with its camera choice as its lobby's source."""
    record = _chosen(records, folder, sources.get("lobby"))
    if record is not None:
        sources = {**sources, "lobby": f"mod:{record['id']}"}
    return sources


def overlay_selection(library: ModLibrary, variant: str, pin: Pin, config: dict | None, folder: dict,
                      result: dict) -> dict:
    """A variant's saved choices (``ModLibrary.selection``) with its camera choice as its lobby's: installed when both
    its art and its prefab are."""
    records = cameras(library, variant)
    art = result.get("lobby")
    record = _chosen(records, folder, (art or {}).get("source")) if records else None
    if record is None:
        return result
    meta = meta_of(record)
    prefab = library._selection_of(pin, config, meta["slot"], folder[meta["slot"]])
    installed = prefab["installed"] and (art["installed"] if art else profiles.enabled(config))
    error = None if installed else ((art or {}).get("error") or prefab.get("error"))
    result["lobby"] = {"source": f"mod:{record['id']}", "installed": installed, "error": error,
                       **({"incomplete": True} if (art or {}).get("incomplete") else {})}
    return result
