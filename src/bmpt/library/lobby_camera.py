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
from collections.abc import Callable
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
        patch_prefab(base, output, root, k, (ox, oy), crc=library.settings.crc_needed(live))
        number = 1 + max((meta_of(r).get("number", 0) for r in cameras(library, variant)), default=0)
        meta = {"variant": variant, "core": core, "source": source, "scale": k, "offset": [ox, oy], "root": root,
                "prefab": live.name, "slot": slot, "base": base_source, "number": number}
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
    """The game's prefab file when a game update replaced the one a camera was made on, else None."""
    meta = meta_of(record)
    paths = library._index(pin).get(meta["slot"], [])
    if len(paths) != 1 or paths[0].name == record["files"][0]:
        return None
    return paths[0]


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
                    patch_prefab(library._official_for(live, pin), output, meta["root"], meta["scale"],
                                 tuple(meta["offset"]), crc=library.settings.crc_needed(live))
                    old = library._paths(record)[0]
                    target = old.parent / live.name
                    digest = library._hashes.get(output).sha1
                    atomic_copy(output, target)
                    verify_file(target, digest)
                record["files"] = [live.name]
                meta["prefab"] = live.name
                library._save()
                if old != target:
                    old.unlink(missing_ok=True)
                log(f"Lobby camera made again on the updated prefab: {live.name}")
            except (ServiceError, OSError) as error:
                log_failure(logger, logging.WARNING, "Lobby camera kept on its earlier prefab", error)


def preview_files(library: ModLibrary, record: dict, pin: Pin) -> tuple[Path, Path | None]:
    """What a camera's preview reads: its own prefab file, or, after a game update it was not made again for yet, the
    game's official prefab to frame on the fly (second item)."""
    live = stale(library, record, pin)
    return library._paths(record)[0], (library._official_for(live, pin, full=False) if live is not None else None)


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
