"""Packed typetree array writes: the bytes UnityPy's own element-by-element write gives."""

import numpy as np
import pytest
from UnityPy.helpers import TypeTreeHelper as helper
from UnityPy.helpers.TypeTreeNode import TypeTreeNode
from UnityPy.streams.EndianBinaryWriter import EndianBinaryWriter

from bmpt.gameassets import typetree_writes

ALIGN = 0x4000


def _node(kind, name, children=(), meta=0):
    return TypeTreeNode(0, kind, name, -1, 1, list(children), m_MetaFlag=meta)


def _vector(name, element, meta=0, array_meta=0):
    return _node("vector", name, [_node("Array", "Array", [_node("int", "size"), element], array_meta)], meta)


def _struct(kind, name, *fields, meta=0):
    return _node(kind, name, fields, meta)


def _tree():
    vector3 = lambda name: _struct("Vector3f", name, _node("float", "x"), _node("float", "y"), _node("float", "z"))  # noqa: E731
    root = _struct(
        "Test",
        "Base",
        _vector("words", _node("unsigned int", "data")),
        _vector("floats", _node("float", "data")),
        _node("UInt8", "flag"),
        _vector("bytes", _node("UInt8", "data"), array_meta=ALIGN),
        _vector("aligned elements", _node("UInt8", "data", meta=ALIGN)),
        _vector("bools", _node("bool", "data"), meta=ALIGN),
        _vector("longs", _node("SInt64", "data")),
        _vector("deltas", _struct("ValueDelta", "data", _node("float", "m_Start"), _node("float", "m_Stop"))),
        _vector("single", _struct("Wrapper", "data", _node("int", "value"))),
        _vector("keys", _struct("Keyframe", "data", _node("float", "time"), vector3("value"), _node("int", "mode"))),
        _vector(
            "aligned field",
            _struct("Item", "data", _node("int", "a"), _node("UInt8", "b", meta=ALIGN)),
        ),
        _vector("names", _node("string", "data", [_vector("Array", _node("char", "data"))], meta=ALIGN)),
        _vector("empty", _node("float", "data")),
        _vector("empty pairs", _struct("Pair", "data", _node("int", "a"), _node("float", "b"))),
    )
    value = {
        "words": [0, 1, 2**32 - 1, 7],
        "floats": [0.1, -2.5, 3e30, float("inf")],
        "flag": 3,
        "bytes": bytes([1, 2, 3, 4, 5]),
        "aligned elements": [9, 8],
        "bools": [True, False, 1],
        "longs": list(np.array([-(2**40), 5], dtype=np.int64)),
        "deltas": [{"m_Start": 0.5, "m_Stop": np.float32(1.25)}, {"m_Start": -1.0, "m_Stop": 2.0}],
        "single": [{"value": 4}, {"value": -4}],
        "keys": [{"time": 0.25, "value": {"x": 1.0, "y": 2.0, "z": 3.0}, "mode": 2}],
        "aligned field": [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
        "names": ["a", "bcd"],
        "empty": [],
        "empty pairs": [],
    }
    return root, value


def _write(root, value, endian, write_value):
    writer = EndianBinaryWriter(b"", endian)
    write_value(value, root, writer, helper.TypeTreeConfig(True, None, False))
    return writer.bytes


@pytest.mark.parametrize("endian", ["<", ">"])
def test_packed_arrays_write_unitypys_bytes(endian, monkeypatch):
    typetree_writes.install()
    fast = helper.write_value
    assert hasattr(fast, "fast_of")
    root, value = _tree()
    packed = _write(root, value, endian, fast)
    plans = {child.m_Name: getattr(child, "_bmpt_write_plan", None) for child in root.m_Children}
    assert plans["words"] == "I" and plans["bytes"] == "B" and plans["deltas"][0] == "ff"
    assert plans["keys"][0] == "ffffi"
    assert plans["aligned elements"] is None and plans["aligned field"] is None and plans["names"] is None
    monkeypatch.setattr(helper, "write_value", fast.fast_of)  # UnityPy's own write, recursing through itself
    plain = _write(*_tree(), endian, fast.fast_of)
    assert packed == plain
    assert len(packed) % 4 == 0


def test_install_is_idempotent():
    typetree_writes.install()
    first = helper.write_value
    typetree_writes.install()
    assert helper.write_value is first


def test_objects_read_by_attribute_take_unitypys_write():
    typetree_writes.install()
    root = _vector("deltas", _struct("ValueDelta", "data", _node("float", "m_Start"), _node("float", "m_Stop")))

    class Delta:
        def __init__(self, start, stop):
            self.m_Start, self.m_Stop = start, stop

    values = [Delta(0.5, 1.0), Delta(2.0, 3.0)]
    writer = EndianBinaryWriter(b"", "<")
    helper.write_value(values, root, writer, helper.TypeTreeConfig(False, None, False))
    assert writer.bytes == np.array([2], "<i4").tobytes() + np.array([0.5, 1.0, 2.0, 3.0], "<f4").tobytes()
