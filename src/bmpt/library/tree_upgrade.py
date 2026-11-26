"""Move Mesh and AnimationClip values into a supported target typetree."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from UnityPy.helpers.TypeTreeHelper import FUNCTION_WRITE_MAP

from bmpt.engine import Bundle, LogFunc, Obj, RawAssetBytes, install_raw_content

if TYPE_CHECKING:
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode


class TreeUpgradeError(ValueError):
    """The source values or resource files cannot be carried into the target."""


def _mesh_lod_info(source: dict) -> dict:
    return {
        "m_LodSelectionCurve": {"m_LodSlope": 0.0, "m_LodBias": 0.0},
        "m_NumLevels": 1,
        "m_SubMeshes": [{"m_Levels": [{"m_IndexStart": 0, "m_IndexCount": 0}]} for _ in source["m_SubMeshes"]],
    }


_DEFAULTS = {
    ("Mesh", "m_CookingOptions"): 30,
    ("Mesh", "m_MeshLodInfo"): _mesh_lod_info,
    ("AnimationClip", "m_FloatCurves", "[]", "flags"): 0,
    ("AnimationClip", "m_PPtrCurves", "[]", "flags"): 0,
    ("AnimationClip", "m_MuscleClip", "m_Clip", "data", "m_StreamedClip", "discreteCurveCount"): 0,
    ("AnimationClip", "m_ClipBindingConstant", "genericBindings", "[]", "isIntCurve"): 0,
    ("AnimationClip", "m_ClipBindingConstant", "genericBindings", "[]", "isSerializeReferenceCurve"): 0,
}
_DROPPED_FIELDS: frozenset[tuple[str, ...]] = frozenset()
UPGRADE_TYPES = frozenset({"Mesh", "AnimationClip"})


def upgrade_tree(source: dict, target: TypeTreeNode) -> dict:
    """Project source values onto the target layout, filling explicitly supported additions.

    Args:
        source: Object values read with the source's own typetree.
        target: Root of the target object's typetree.

    Raises:
        TreeUpgradeError: A field is missing, would be lost, or has an incompatible shape.
    """
    return _project(source, target, (target.m_Type,), source)


def _project(value: Any, node: TypeTreeNode, path: tuple[str, ...], source: dict) -> Any:
    children = node.m_Children
    if node.m_Type in FUNCTION_WRITE_MAP:
        if not isinstance(value, (dict, list, tuple)):
            return value
    elif children and children[0].m_Type == "Array":
        element = children[0].m_Children[1]
        if isinstance(value, bytes) and element.m_Type in {"UInt8", "char"}:
            return value
        if isinstance(value, (list, tuple)):
            return [_project(item, element, (*path, "[]"), source) for item in value]
    elif node.m_Type == "pair":
        if isinstance(value, (list, tuple)) and len(value) == len(children) == 2:
            return tuple(_project(item, child, (*path, child.m_Name), source) for item, child in zip(value, children))
    elif isinstance(value, dict):
        fields = {child.m_Name: child for child in children}
        for name in value.keys() - fields.keys():
            if (*path, name) not in _DROPPED_FIELDS:
                raise TreeUpgradeError(f"No target field for {'.'.join((*path, name))}")
        result = {}
        for name, child in fields.items():
            field_path = (*path, name)
            if name in value:
                field_value = value[name]
            elif field_path in _DEFAULTS:
                default = _DEFAULTS[field_path]
                field_value = default(source) if callable(default) else default
            else:
                raise TreeUpgradeError(f"No default for {'.'.join(field_path)}")
            result[name] = _project(field_value, child, field_path, source)
        return result
    raise TreeUpgradeError(f"Incompatible value at {'.'.join(path)}")


def _stream_name(stream: dict) -> str:
    return stream["path"].replace("\\", "/").rsplit("/", 1)[-1]


def _stream_range(stream: dict, payload: bytes | None) -> memoryview:
    offset, size = stream["offset"], stream["size"]
    if payload is None or offset < 0 or size < 0 or offset + size > len(payload):
        raise TreeUpgradeError("The mesh stream is missing or its byte range is invalid")
    return memoryview(payload)[offset : offset + size]


def same_mesh_stream(source: dict, content: bytes, target: dict, bundle: Bundle) -> bool:
    """Compare companion bytes for a mesh whose shared typetree fields match."""
    stream = source.get("m_StreamData")
    if not stream or not stream["path"]:
        return True
    name = _stream_name(stream)
    entry = bundle.env.file.files.get(name)
    companions = content.companions if isinstance(content, RawAssetBytes) else {}
    return _stream_range(stream, companions.get(name)) == _stream_range(
        target["m_StreamData"], getattr(entry, "bytes", None)
    )


def _install_mesh_stream(stream: dict, content: bytes, target: Obj, bundle: Bundle, log: LogFunc) -> None:
    name = _stream_name(stream)
    companions = content.companions if isinstance(content, RawAssetBytes) else {}
    payload = companions.get(name)
    _stream_range(stream, payload)
    files = bundle.env.file.files
    cab = next(name for name, entry in files.items() if entry is target.assets_file)
    if name in files and getattr(files[name], "bytes", None) != payload:
        # Keep the resource used by the target's other meshes at its original name.
        name = f"BMPT-{hashlib.sha256(payload).hexdigest()}.{name.rsplit('.', 1)[-1]}"
        if name in files and getattr(files[name], "bytes", None) != payload:
            raise TreeUpgradeError("The mesh stream resource name is already in use")
    install_raw_content(RawAssetBytes(companions={name: payload}), bundle.env, log)
    stream["path"] = f"archive:/{cab}/{name}"


def write_upgrade(source: dict, content: bytes, target: Obj, bundle: Bundle, log: LogFunc) -> None:
    """Save upgraded values and attach any required Mesh resource to the destination bundle.

    Args:
        source: Object values read with the source's own typetree.
        content: Extracted object bytes with any resource companions.
        target: Object whose layout and serialized file the result uses.
        bundle: Bundle containing the target object and its resources.
        log: Receives resource installation messages.

    Raises:
        TreeUpgradeError: The target schema or a required stream cannot be used.
    """
    serialized = target.serialized_type
    if serialized is None or serialized.node is None:
        raise TreeUpgradeError("The target has no embedded typetree")
    tree = upgrade_tree(source, serialized.node)
    if target.type.name == "Mesh" and (stream := tree.get("m_StreamData")) and stream["path"]:
        _install_mesh_stream(stream, content, target, bundle, log)
    target.save_typetree(tree)
