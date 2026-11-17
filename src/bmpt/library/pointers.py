"""Cross-bundle references of a bundle and whether their targets are there.

An object in one bundle points into another through a PPtr: a file id, an index into its serialized file's external
table (a CAB name), and a path id, the object's id in that file. A game update can rebuild a referenced bundle under
the same CAB name with objects dropped or given other ids. A mod file made before then still points at the old ids:
the game loads it without an error and draws nothing where the pointer was. These readers find such pointers. They
read type trees and object headers only: no image data is read or decoded, and nothing is resolved by name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

import UnityPy.classes as unity_classes

logger = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class Pointer:
    """One cross-bundle pointer: the CAB it points into (lower case), the object's path id there and the type the
    field declares (``PPtr<Material>`` declares ``Material``)."""

    cab: str
    path_id: int
    declared: str


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _has_pointer(node, seen: dict[int, bool]) -> bool:
    key = id(node)
    if key not in seen:
        seen[key] = node.m_Type.startswith("PPtr<") or any(_has_pointer(child, seen) for child in node.m_Children)
    return seen[key]


def _walk(node, value, seen: dict[int, bool]) -> Iterator[tuple[str, dict]]:
    """The (declared type, value) of every PPtr in ``value``, read along its type tree ``node``."""
    if not _has_pointer(node, seen):
        return
    if node.m_Type.startswith("PPtr<"):
        if isinstance(value, dict):
            yield node.m_Type[5:-1].lstrip("$"), value
        return
    children = node.m_Children
    if isinstance(value, dict):
        for child in children:
            if child.m_Name in value:
                yield from _walk(child, value[child.m_Name], seen)
        return
    if isinstance(value, (list, tuple)):
        # vector, map and set hold one Array node whose children are its size and its element
        array = next((child for child in children if child.m_Type == "Array" or child.m_Name == "Array"), None)
        if array is None and node.m_Type == "Array":
            array = node
        if array is not None and len(array.m_Children) >= 2:
            element = array.m_Children[1]
            for item in value:
                yield from _walk(element, item, seen)
            return
        if node.m_Type == "pair" and len(children) == 2 and len(value) == 2:
            yield from _walk(children[0], value[0], seen)
            yield from _walk(children[1], value[1], seen)


def external_pointers(env) -> set[Pointer]:
    """Every pointer of the bundle's objects into another serialized file (a CAB of another bundle). Objects whose
    type holds no pointer (textures, meshes, text, audio) are never read; one whose type tree cannot be read gives
    none, as it gives no evidence either way."""
    found: set[Pointer] = set()

    def visit(file) -> None:
        if hasattr(file, "target_platform"):
            externals = [_basename(ref.path) for ref in file.externals]
            if not externals:
                return
            seen: dict[int, bool] = {}
            for obj in file.objects.values():
                try:
                    node = obj._get_typetree_node()
                    if not _has_pointer(node, seen):
                        continue
                    tree = obj.read_typetree(node, check_read=False)
                except Exception:  # noqa: BLE001 - an unreadable object gives no evidence
                    logger.debug("No type tree read for object %s", obj.path_id, exc_info=True)
                    continue
                for declared, pointer in _walk(node, tree, seen):
                    file_id, path_id = pointer.get("m_FileID"), pointer.get("m_PathID")
                    if (
                        isinstance(file_id, int)
                        and isinstance(path_id, int)
                        and path_id
                        and 0 < file_id <= len(externals)
                    ):
                        found.add(Pointer(externals[file_id - 1], path_id, declared))
            return
        for child in getattr(file, "files", {}).values():
            visit(child)

    visit(env)
    return found


def _class(name: str):
    value = getattr(unity_classes, name, None)
    return value if isinstance(value, type) else None


def compatible(declared: str, class_id: int) -> bool:
    """Whether an object of Unity class ``class_id`` can stand where ``PPtr<declared>`` points. A declared type the
    class table does not know (a script type such as ``SkeletonDataAsset``) needs a MonoBehaviour; any other unknown
    gives no evidence."""
    from UnityPy.enums import ClassIDType

    try:
        target = _class(ClassIDType(class_id).name)
    except ValueError:
        return True
    expected = _class(declared)
    if target is None:
        return True
    if expected is None:
        return target is unity_classes.MonoBehaviour
    return issubclass(target, expected)


def dangling(pointers: Iterable[Pointer], layout: Callable[[str], dict[int, int] | None]) -> set[Pointer]:
    """The pointers whose target object is not in its CAB, or is of a type the field cannot hold. ``layout`` gives a
    CAB's objects ({path id: class id}) as the game has it now, None for a CAB it cannot find: those pointers are
    left to the missing-part checks."""
    broken: set[Pointer] = set()
    layouts: dict[str, dict[int, int] | None] = {}
    for pointer in pointers:
        if pointer.cab not in layouts:
            layouts[pointer.cab] = layout(pointer.cab)
        objects = layouts[pointer.cab]
        if objects is None:
            continue
        class_id = objects.get(pointer.path_id)
        if class_id is None or not compatible(pointer.declared, class_id):
            broken.add(pointer)
    return broken
