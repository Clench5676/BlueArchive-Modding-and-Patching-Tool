"""Write Unity typetree arrays of plain values in one pack instead of one call per value.

UnityPy's ``TypeTreeHelper.write_value`` writes every element of an array on its own: a function call, a
``struct.pack`` and a stream write (with a ``tell()``) per number. An object written back after an edit (a clip's
streamed curves, a mesh's buffers) holds hundreds of thousands of them, most of a costume build's serializing time.
:func:`install` replaces ``write_value`` with a wrapper that packs such an array at once: an array of one fixed-size
number type, or of a class made only of such numbers (``ValueDelta``, ``float3``, ``PPtr``), when no element or field
is aligned on its own. The bytes are those UnityPy writes: the same struct codes and byte order, the length first, the
array's alignment after. Every other node goes to UnityPy's own ``write_value``, which recurses through the wrapper.
"""

from __future__ import annotations

import struct
import threading
from operator import itemgetter
from typing import Any, Callable

# struct codes of the number types, and the EndianBinaryWriter method UnityPy writes each with (checked at install)
_CODES = {
    "SInt8": ("b", "write_byte"),
    "UInt8": ("B", "write_u_byte"),
    "char": ("B", "write_u_byte"),
    "short": ("h", "write_short"),
    "SInt16": ("h", "write_short"),
    "unsigned short": ("H", "write_u_short"),
    "UInt16": ("H", "write_u_short"),
    "int": ("i", "write_int"),
    "SInt32": ("i", "write_int"),
    "unsigned int": ("I", "write_u_int"),
    "UInt32": ("I", "write_u_int"),
    "Type*": ("I", "write_u_int"),
    "long long": ("q", "write_long"),
    "SInt64": ("q", "write_long"),
    "unsigned long long": ("Q", "write_u_long"),
    "UInt64": ("Q", "write_u_long"),
    "FileSize": ("Q", "write_u_long"),
    "float": ("f", "write_float"),
    "double": ("d", "write_double"),
    "bool": ("?", "write_boolean"),
}
_ALIGN = 0x4000  # kAlignBytes
_install = threading.Lock()
_PLAN = "_bmpt_write_plan"  # the attribute a node keeps its plan in (UnityPy's nodes are unhashable)
_UNSET = object()
# the types UnityPy writes by a function of its own (numbers, strings, raw bytes) or by a rule of its own
_SPECIAL: set[str] = {"pair", "ReferencedObject"}


def _aligned(node: Any) -> bool:
    return bool((node.m_MetaFlag or 0) & _ALIGN)


def _fields(node: Any, path: tuple[str, ...]) -> list[tuple[tuple[str, ...], str]] | None:
    """The (key path, struct code) of each number of a class made only of unaligned numbers, else None."""
    if _aligned(node):
        return None
    if not node.m_Children:
        code = _CODES.get(node.m_Type)
        return [(path, code[0])] if code else None
    if node.m_Type in _SPECIAL or node.m_Children[0].m_Type == "Array":
        return None
    out: list[tuple[tuple[str, ...], str]] = []
    for child in node.m_Children:
        if child.m_Type == "ManagedReferencesRegistry":
            return None
        inner = _fields(child, (*path, child.m_Name))
        if inner is None:
            return None
        out += inner
    return out or None


def _getter(fields: list[tuple[tuple[str, ...], str]]) -> Callable[[dict], tuple]:
    """A function giving an element's numbers in field order."""
    if all(len(path) == 1 for path, _ in fields):
        names = [path[0] for path, _ in fields]
        if len(names) == 1:
            one = names[0]
            return lambda value: (value[one],)
        return itemgetter(*names)
    paths = [path for path, _ in fields]

    def get(value: dict) -> tuple:
        out = []
        for path in paths:
            item = value
            for name in path:
                item = item[name]
            out.append(item)
        return tuple(out)

    return get


def _plan(node: Any) -> Any:
    """What the wrapper does with ``node``: None (UnityPy's own write), a struct code, or (struct codes, getter)."""
    if node.m_Type in _SPECIAL:
        return None
    if not node.m_Children or node.m_Children[0].m_Type != "Array":
        return None
    element = node.m_Children[0].m_Children[1]
    if _aligned(element):
        return None
    if not element.m_Children:
        code = _CODES.get(element.m_Type)
        return code[0] if code else None
    fields = _fields(element, ())
    if fields is None:
        return None
    return "".join(code for _, code in fields), _getter(fields)


def _fast_write(original: Callable[..., Any]) -> Callable[..., Any]:
    def write_value(value: Any, node: Any, writer: Any, config: Any) -> None:
        plan = getattr(node, _PLAN, _UNSET)
        if plan is _UNSET:
            plan = _plan(node)
            try:
                setattr(node, _PLAN, plan)
            except AttributeError:  # a node without a __dict__ plans again next time
                pass
        if plan is None:
            return original(value, node, writer, config)
        endian = writer.endian
        if isinstance(plan, str):
            writer.write(struct.pack(f"{endian}i{len(value)}{plan}", len(value), *value))
        else:
            if not all(isinstance(item, dict) for item in value):  # UnityPy reads objects' fields by attribute
                return original(value, node, writer, config)
            codes, get = plan
            packer = struct.Struct(endian + codes)
            writer.write(struct.pack(f"{endian}i", len(value)) + b"".join([packer.pack(*get(item)) for item in value]))
        if _aligned(node) or _aligned(node.m_Children[0]):
            writer.align_stream()
        return None

    write_value.fast_of = original  # type: ignore[attr-defined]
    return write_value


def install() -> None:
    """Install the wrapper once; leave UnityPy as it is when its writer methods are not the ones this assumes."""
    from UnityPy.helpers import TypeTreeHelper as helper
    from UnityPy.streams.EndianBinaryWriter import EndianBinaryWriter

    with _install:
        if hasattr(helper.write_value, "fast_of"):
            return
        table = helper.FUNCTION_WRITE_MAP
        if any(table.get(name) is not getattr(EndianBinaryWriter, method) for name, (_, method) in _CODES.items()):
            return
        _SPECIAL.update(table)
        helper.write_value = _fast_write(helper.write_value)
