"""Identify the set pieces modelled into a chibi's costume: a wheelchair, a telescope she sets up, a prop for one skill.

Some costume meshes hold things that are no clothing: what the chibi sits in, and things her clips put beside her for
a moment. Nothing in the files marks them (they share the body's mesh, materials and shading), so they are recognised
by how the mesh is built and how the chibi's own clips treat them. A set piece is a branch of bones outside the Biped
body (no body bone among its ancestors or below it) that is

1. loose: the costume's vertices skinned to it are skinned to it alone, and no triangle (nor a vertex at the same
   place, across a seam) joins them to the rest of the costume: a skirt or a train hanging off such bones is sewn
   to the body and stays clothing;
2. and either shown at times: the idle draws it at less than ``SHOWN`` of its size (the clips bring it out for a
   skill or a pose);
3. or a seat: the idle draws it, and in the idle and ``SHARE`` or more of the probe poses drawing it its lowest
   point lies within ``GROUND`` of the floor, it reaches ``TALL`` or more above it and its centre lies within
   ``UNDER`` of the pelvis seen from above (what the chibi sits in; a shoe's sole is skinned to the foot and is
   never loose).

A set piece belongs to the animation of the slot it was made for, not to the costume. On a costume switch
(``chibi_adapt``) the incoming costume's set pieces draw nothing (the slot's clips neither seat anyone in them nor
bring them out), and the slot's own stay: each of its costume meshes holding one is drawn again by a renderer of its
own, with every other triangle taken out, on the bones the slot's clips key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .chibi_adapt.skeleton import BIPED_CHAIN, BIPED_ROOT

if TYPE_CHECKING:
    from .chibi_adapt.costume import Costume, Flat
    from .chibi_adapt.skeleton import Skeleton

SHOWN = 0.25  # drawn at this share of its modelled size or more: shown
SHARE = 0.75  # standing in this share of the poses drawing it, or more
GROUND = 0.05  # its lowest point within this of the floor (metres): it stands on it
TALL = 0.25  # ... and its highest point at least this far above the floor: a thing lying flat (a shadow) is none
UNDER = 0.15  # ... and its centre this near the pelvis, seen from above
SEAM = 1e-4  # vertices this near each other are one place (a seam's two sides)
SMALL = 12  # a branch skinning fewer vertices is not judged


@dataclass
class SetPiece:
    """One set piece: the branch's top node and the costume vertices it draws (rows of the flattened costume)."""

    top: int
    rows: np.ndarray
    kind: str  # "times" (the clips show it at times) or "seat"


def _free_tops(skeleton: Skeleton) -> np.ndarray:
    """Per node, the top of its branch outside the Biped body, or -1 for a node of the body's own tree."""
    nodes = skeleton.nodes
    chain = [bool(BIPED_CHAIN.match(node.name)) for node in nodes]
    holds = [False] * len(nodes)  # a body bone below it: a rig container
    for i, node in enumerate(nodes):
        if chain[i]:
            p = node.parent
            while p is not None and not holds[p]:
                holds[p] = True
                p = nodes[p].parent
    free = [
        not chain[i]
        and not holds[i]
        and not nodes[i].name.startswith(BIPED_ROOT)  # a weapon mount a vendor named with the Biped's prefix
        and skeleton.chain_ancestor(i) is None
        for i in range(len(nodes))
    ]
    tops = np.full(len(nodes), -1)
    for i, node in enumerate(nodes):
        if not free[i]:
            continue
        top = i
        while nodes[top].parent is not None and free[nodes[top].parent]:
            top = nodes[top].parent
        tops[i] = top
    return tops


def _islands(flat: Flat) -> np.ndarray:
    """Per vertex, the island it belongs to: vertices joined by triangles or lying at one place."""
    count = len(flat.vertices)
    link = np.arange(count)

    def find(a: int) -> int:
        while link[a] != a:
            link[a] = link[link[a]]
            a = int(link[a])
        return a

    def join(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            link[max(ra, rb)] = min(ra, rb)

    for a, b, c in flat.triangles.tolist():
        join(a, b)
        join(b, c)
    cells = np.round(flat.vertices / SEAM).astype(np.int64)
    order = np.lexsort(cells.T)
    same = (cells[order][1:] == cells[order][:-1]).all(axis=1)
    for i in np.where(same)[0].tolist():
        join(int(order[i]), int(order[i + 1]))
    return np.array([find(i) for i in range(count)])


def set_pieces(
    costume: Costume, flat: Flat, idle: list[np.ndarray] | None, poses: list[list[np.ndarray]]
) -> list[SetPiece]:
    """Find the set pieces in a chibi's costume by the module's rule.

    Args:
        costume: The chibi's costume parts on its own skeleton.
        flat: The costume flattened at the skeleton's rest (``flatten``).
        idle: First idle-frame world matrices of the chibi's own idle; None finds nothing.
        poses: World poses of the chibi's own clips (its probes), judged beside the idle.
    """
    if idle is None or not len(flat.vertices):
        return []
    skeleton = costume.skeleton
    rest = skeleton.world()
    tops = _free_tops(skeleton)
    weighted = (flat.weights > 0) & (flat.joints >= 0)
    of = np.where(weighted, tops[np.maximum(flat.joints, 0)], -2)  # per influence: its branch, -1 body, -2 none
    high = of.max(axis=1)
    alone = (np.where(weighted, of, high[:, None]) == high[:, None]).all(axis=1) & weighted.any(axis=1)
    branch = np.where(alone, high, -1)  # per vertex: the branch it is skinned to alone
    if not (branch >= 0).any():
        return []
    island = _islands(flat)
    mixed = set(island[branch < 0].tolist())  # islands holding a vertex of the body
    pelvis = skeleton.first("Bip001 Pelvis")
    out = []
    for top in sorted(set(branch[branch >= 0].tolist())):
        rows = np.where(branch == top)[0]
        islands = set(island[rows].tolist())
        if len(rows) < SMALL or islands & mixed or len(set(branch[np.isin(island, sorted(islands))].tolist())) > 1:
            continue
        dominant = flat.joints[rows, np.argmax(flat.weights[rows], axis=1)]
        bones = sorted(set(dominant.tolist()))
        column = np.searchsorted(bones, dominant)
        inverse = np.array([np.linalg.inv(rest[b]) for b in bones])
        points = np.c_[flat.vertices[rows], np.ones(len(rows))]

        def size(pose: list[np.ndarray]) -> float:
            """The largest share of its rest size a bone of the branch is drawn at."""
            return max(
                (abs(np.linalg.det(pose[b][:3, :3])) / max(abs(np.linalg.det(rest[b][:3, :3])), 1e-12)) ** (1 / 3)
                for b in bones
            )

        def placed(pose: list[np.ndarray]) -> np.ndarray:
            moves = np.array([pose[b] for b in bones]) @ inverse
            return np.einsum("nij,nj->ni", moves[column], points)[:, :3]

        if size(idle) < SHOWN:
            out.append(SetPiece(top, rows, "times"))
            continue
        shown = [pose for pose in [idle, *poses] if size(pose) >= SHOWN]
        drawn = [placed(pose) for pose in shown]

        def seated(pose: list[np.ndarray], at: np.ndarray) -> bool:
            """Whether the pose stands the branch on the floor below the pelvis."""
            if pelvis is None or float(at[:, 1].min()) > GROUND or float(at[:, 1].max()) < TALL:
                return False
            return float(np.linalg.norm((at.mean(axis=0) - pose[pelvis][:3, 3])[[0, 2]])) <= UNDER

        if seated(idle, drawn[0]) and sum(seated(p, at) for p, at in zip(shown, drawn)) >= SHARE * len(shown):
            out.append(SetPiece(top, rows, "seat"))
    return out


def part_triangles(costume: Costume, flat: Flat, pieces: list[SetPiece]) -> dict[int, np.ndarray]:
    """Per costume part holding set pieces, their triangles numbered as the part's mesh lists them."""
    if not pieces:
        return {}
    rows = np.zeros(len(flat.vertices), bool)
    for piece in pieces:
        rows[piece.rows] = True
    hit = rows[flat.triangles].any(axis=1) if len(flat.triangles) else np.zeros(0, bool)
    out = {}
    first = 0
    for k, skin in enumerate(costume.skins):
        count = len(skin.triangles) if skin.triangles is not None else 0
        here = np.where(hit[first : first + count])[0]
        if len(here):
            out[k] = here
        first += count
    return out
