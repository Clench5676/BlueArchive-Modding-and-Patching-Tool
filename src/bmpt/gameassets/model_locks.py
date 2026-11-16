"""Hair locks and ears of a head, and which of the slot's own a costume's locks take their motion from.

A model's hair moves because its own clips key every lock, rotation only, in time with the body. A costume put on
another model brings locks the slot's clips do not know. With Optimize Physics each of the costume's locks takes the
keys of the slot's lock that hangs where it does (``model_skirts.Carry``): matched by data alone, never by name:

- a lock is a chain rooted at a secondary bone right below the head; its bones are those along the longest path, from
  its first bone to the farthest one, each at a share of the path's length;
- its place is the azimuth of its middle (halfway from its first bone to its farthest) around the vertical through
  the head, its direction the line from its first bone to its farthest; it hangs when that points within
  ``HANGING`` of straight down, else it stands (an ear, an antenna);
- an incoming lock takes the slot lock of the same kind nearest by ``cost``: the azimuth difference per
  ``AZIMUTH_UNIT`` plus the angle between the directions per ``DIRECTION_UNIT``, and none past ``MATCH_COST``.
  Several incoming locks may share one slot lock.

Pure numpy; the model plumbing is in ``model_adapt.springs``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

AZIMUTH_UNIT = 45.0     # degrees of azimuth that cost 1
DIRECTION_UNIT = 60.0   # degrees between two directions that cost 1
# the largest cost a match may have: a lock up to about halfway between two of the slot's (whose locks sit 55 to 70
# degrees apart around the head) with a direction within some 10 degrees, or right below one 70 degrees off. Over
# 374 sampled pairs 71 % of the incoming locks find a slot lock under 1 and 78 % under 1.2; past that each tenth
# adds about 2 %, a lock already nearer a neighbour's place than its own.
MATCH_COST = 1.2
MIN_LENGTH = 0.01       # a lock shorter than this (metres, first bone to farthest) matches nothing
HANGING = math.radians(60)
UP = np.array([0.0, 1.0, 0.0])  # Unity's vertical (the prefab's +Y)


@dataclass
class Lock:
    """A lock at rest (the prefab root's space): its nodes from its first bone to its farthest, each one's share of
    the path's length (``span``, along its bones), the straight length from first to farthest, the azimuth of its
    middle about the head (degrees), its direction (unit) and whether it hangs."""

    root: int
    path: list[int]
    shares: np.ndarray
    span: float
    length: float
    azimuth: float
    direction: np.ndarray
    hanging: bool


def lock_of(root: int, members: Sequence[int], place: Callable[[int], np.ndarray], head: np.ndarray,
            parent: Callable[[int], int | None]) -> Lock:
    """The lock whose first bone is ``root`` and whose bones are ``members`` (``root`` included), with ``place`` the
    rest position of a node, ``head`` the head's and ``parent`` a node's parent."""
    at = place(root)
    far = max(members, key=lambda j: float(np.linalg.norm(place(j) - at)))
    path = [far]
    while path[-1] != root:
        up = parent(path[-1])
        if up is None:
            raise ValueError("a lock's bone outside it")
        path.append(up)
    path.reverse()
    along = np.concatenate([[0.0], np.cumsum([float(np.linalg.norm(place(b) - place(a)))
                                              for a, b in zip(path, path[1:])])])
    total = float(along[-1])
    tip = place(far)
    d = tip - at
    length = float(np.linalg.norm(d))
    middle = (at + tip) / 2 - head
    flat = middle - (middle @ UP) * UP
    azimuth = math.degrees(math.atan2(float(flat[0]), float(flat[2])))
    direction = d / length if length > 1e-9 else np.zeros(3)
    return Lock(root, path, along / total if total > 1e-9 else np.zeros(len(path)), total, length, azimuth, direction,
                bool(length > 1e-9 and float(direction @ -UP) >= math.cos(HANGING)))


def share_of(lock: Lock, node: int, place: Callable[[int], np.ndarray]) -> float:
    """Where ``node`` of a lock lies along it (0 its first bone, 1 its farthest): on the path, its share of the path's
    length; on a side branch, its distance from the first bone as a share of the same (at most 1)."""
    if node in lock.path:
        return float(lock.shares[lock.path.index(node)])
    if lock.span < 1e-9:
        return 0.0
    return min(1.0, float(np.linalg.norm(place(node) - place(lock.root))) / lock.span)


def cost(a: Lock, b: Lock) -> float:
    """How far the slot's lock ``b`` is from lock ``a``: their azimuth difference per ``AZIMUTH_UNIT`` and the angle
    between their directions per ``DIRECTION_UNIT``; infinite between a hanging and a standing lock, or for a lock too
    short to have a direction."""
    if a.length < MIN_LENGTH or b.length < MIN_LENGTH or a.hanging != b.hanging:
        return math.inf
    turn = abs((a.azimuth - b.azimuth + 180.0) % 360.0 - 180.0)
    between = math.degrees(math.acos(float(np.clip(a.direction @ b.direction, -1.0, 1.0))))
    return turn / AZIMUTH_UNIT + between / DIRECTION_UNIT


def match(incoming: Sequence[Lock], host: Sequence[Lock], limit: float = MATCH_COST) -> list[tuple[int | None, float]]:
    """Per incoming lock, the index of the slot lock it takes its motion from and the cost (None and the best cost,
    possibly infinite, when none is within ``limit``)."""
    out: list[tuple[int | None, float]] = []
    for a in incoming:
        costs = [cost(a, b) for b in host]
        best = int(np.argmin(costs)) if costs else None
        value = costs[best] if best is not None else math.inf
        out.append((best if value <= limit else None, value))
    return out


def head_locks(nodes: Sequence[tuple[str, int | None]], head: int,
               rest: Mapping[int, np.ndarray] | Sequence[np.ndarray], roots: Sequence[int] | None = None) -> list[Lock]:
    """The locks below a skeleton's ``head`` (``nodes``: per node its name and parent): each secondary bone right below
    it (a name outside the Biped's ``Bip001``) with its secondary descendants, or the given ``roots`` only. ``rest``:
    the nodes' world matrices at rest."""
    children: dict[int, list[int]] = {}
    for i, (_, parent) in enumerate(nodes):
        if parent is not None:
            children.setdefault(parent, []).append(i)

    def secondary(i: int) -> bool:
        return not nodes[i][0].startswith("Bip001")

    if roots is None:
        roots = [i for i in children.get(head, []) if secondary(i)]
    out = []
    for root in roots:
        members, todo = [], [root]
        while todo:
            j = todo.pop()
            members.append(j)
            todo.extend(c for c in children.get(j, []) if secondary(c))
        out.append(lock_of(root, members, lambda j: np.asarray(rest[j])[:3, 3], np.asarray(rest[head])[:3, 3],
                           lambda j: nodes[j][1]))
    return out
