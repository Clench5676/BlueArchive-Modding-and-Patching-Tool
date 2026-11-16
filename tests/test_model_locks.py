"""Hair locks matched to the slot's own (gameassets.model_locks) and the slot's lock keys carried onto them
(model_skirts.Carry, model_adapt.carried_turns), on made-up skeletons."""

import math

import numpy as np
import pytest

from bmpt.gameassets import model_adapt as ma
from bmpt.gameassets import model_locks as ml
from bmpt.gameassets import model_skirts as ms
from bmpt.gameassets.model_adapt import trs


def _at(x, y, z):
    return trs((x, y, z), (0, 0, 0, 1), (1, 1, 1))


def _turn(axis, degrees):
    return ms.axis_angle(np.asarray(axis, float) / np.linalg.norm(axis), math.radians(degrees))


def _host():
    """A head at the origin with a front, a left and a back lock hanging, an ear standing on the left, a one-bone hat
    and a Biped bone below it (nodes: name and parent; rest world matrices)."""
    nodes = [("Root", None), ("Bip001 Head", 0),
             ("f_1", 1), ("f_2", 2), ("l_1", 1), ("l_2", 4), ("b_1", 1), ("b_2", 6), ("ear_1", 1), ("ear_2", 8),
             ("hat", 1), ("Bip001 HeadNub", 1)]
    places = [(0, 0, 0), (0, 0, 0), (0, 0, 0.1), (0, -0.08, 0.1), (0.1, 0, 0), (0.1, -0.08, 0), (0, 0, -0.1),
              (0, -0.06, -0.1), (0.08, 0.05, 0), (0.08, 0.09, 0), (0, 0.12, 0), (0, 0.2, 0)]
    return nodes, [_at(*p) for p in places]


def _lock(root, tip, middle=None):
    """A lock of its own skeleton (nodes 0, 1, 2 or 0, 1: root, (middle), tip) about a head at the origin."""
    points = [root, *([middle] if middle is not None else []), tip]
    return ml.lock_of(0, list(range(len(points))), lambda j: np.asarray(points[j], float), np.zeros(3),
                      lambda j: j - 1 if j else None)


def test_the_locks_below_a_head_with_their_place_kind_and_shares():
    nodes, rest = _host()
    locks = {nodes[lock.root][0]: lock for lock in ml.head_locks(nodes, 1, rest)}
    assert sorted(locks) == ["b_1", "ear_1", "f_1", "hat", "l_1"]  # secondary bones right below the head only
    assert locks["f_1"].path == [2, 3] and list(locks["f_1"].shares) == [0.0, 1.0]
    assert locks["f_1"].length == pytest.approx(0.08) and locks["f_1"].hanging
    assert locks["f_1"].azimuth == pytest.approx(0.0) and locks["l_1"].azimuth == pytest.approx(90.0)
    assert abs(locks["b_1"].azimuth) == pytest.approx(180.0)
    assert not locks["ear_1"].hanging and locks["ear_1"].azimuth == pytest.approx(90.0)
    assert locks["hat"].length == 0.0
    # shares follow the length along the bones; a side branch counts its distance from the first bone
    lock = _lock((0, 0, 0), (0, -0.3, 0), middle=(0, -0.1, 0))
    assert list(lock.shares) == pytest.approx([0.0, 1 / 3, 1.0])
    place = lambda j: np.asarray([(0, 0, 0), (0, -0.1, 0), (0, -0.3, 0), (0.15, 0, 0)][j], float)  # noqa: E731
    assert ml.share_of(lock, 1, place) == pytest.approx(1 / 3)
    assert ml.share_of(lock, 3, place) == pytest.approx(0.5)


def test_each_incoming_lock_takes_the_nearest_slot_lock_of_its_kind_within_the_cost_limit():
    nodes, rest = _host()
    host = [lock for lock in ml.head_locks(nodes, 1, rest) if lock.length >= ml.MIN_LENGTH]
    names = [nodes[lock.root][0] for lock in host]
    incoming = [
        _lock((0, 0, 0.1), (0, -0.15, 0.12)),                  # front, longer, slightly forward: f
        _lock((-0.05, 0, 0.087), (-0.05, -0.15, 0.087)),       # front right (-30 degrees): f as well
        _lock((0.087, 0, -0.05), (0.087, -0.2, -0.05)),        # back left (120 degrees): l (30) before b (60)
        _lock((0.07, 0.05, 0.02), (0.07, 0.12, 0.02)),         # an ear standing on the left: the ear
        _lock((-0.08, 0.05, 0), (-0.08, 0.12, 0)),             # an ear on the right: the slot's is 180 degrees off
        _lock((-0.1, 0, 0), (-0.1, -0.15, 0)),                 # right (-90 degrees): f and b are 90 off, no match
        _lock((0, 0, 0.1), (0, -0.005, 0.1)),                  # too short to have a direction
    ]
    matched = ml.match(incoming, host)
    assert [names[i] if i is not None else None for i, _ in matched] == ["f_1", "f_1", "l_1", "ear_1", None, None,
                                                                          None]
    assert matched[1][1] == pytest.approx(30 / 45, abs=0.01)
    assert matched[4][1] == pytest.approx(4.0, abs=0.01) and matched[6][1] == math.inf
    assert matched[5][1] == pytest.approx(2.0, abs=0.01) and matched[5][1] > ml.MATCH_COST
    # a standing lock never takes a hanging one's motion, however near
    assert ml.cost(incoming[3], host[0]) == math.inf


def _carried_spring():
    """A three-bone lock hanging 0.2 m below the head (node 1 of the slot, at the origin), matched to the slot's
    two-bone lock of nodes 2 and 3; its bones at shares 0, 0.5 and 1 of its length."""
    local = np.array([_at(0, 0, 0.1), _at(0, -0.1, 0), _at(0, -0.1, 0)])
    rest = np.array([local[0], local[0] @ local[1], local[0] @ local[1] @ local[2]])
    chains = ms.Chains(1, [-1, 0, 1], local, rest)
    spring = ms.Spring(chains, {0: math.radians(20)})
    spring.carry = ms.Carry(1, np.eye(4), [[2, 3]], [np.array([0.0, 1.0])], [np.array([np.eye(3), np.eye(3)])],
                            {0: 0}, {0: 0.0, 1: 0.5, 2: 1.0}, ma.SWING_MIN)
    return spring


def _poses(head, root_turn, tip_turn):
    """The slot's world matrices (root, head, the slot lock's two nodes) with the head turned ``head`` and the slot
    lock's nodes turned against it."""
    h = np.eye(4)
    h[:3, :3] = head
    out = [np.eye(4), h]
    for turn, place in ((root_turn, (0, 0, 0.1)), (tip_turn, (0, -0.08, 0.1))):
        m = _at(*place)
        m[:3, :3] = turn
        out.append(h @ m)
    return out


def test_the_slot_locks_turns_are_carried_bone_by_bone_along_the_lock_whatever_the_head_does():
    spring = _carried_spring()
    x20, x40 = _turn((1, 0, 0), 20), _turn((1, 0, 0), 40)
    for head in (np.eye(3), _turn((0, 1, 0), 90)):
        poses = [_poses(head, np.eye(3), np.eye(3)), _poses(head, x20, x40)]
        roots, turns = spring.carried(poses)
        assert roots == [0] and [sorted(t) for t in turns] == [[0, 1, 2], [0, 1, 2]]
        assert all(np.allclose(turns[0][b], spring.chains.local[b]) for b in range(3))  # the first frame: at rest
        world = spring.chains.posed(np.eye(4), turns[1])  # the head at rest: the turns against it
        rest = spring.chains.rest
        assert np.allclose(world[0][:3, :3], x20 @ rest[0][:3, :3])                 # share 0: the root's turn
        assert np.allclose(world[1][:3, :3], _turn((1, 0, 0), 30) @ rest[1][:3, :3])  # halfway: between them
        assert np.allclose(world[2][:3, :3], x40 @ rest[2][:3, :3])                 # share 1: the tip's turn
        # the chain rebuilt root to tip: each bone hangs off its parent as the parent turned
        assert np.allclose(world[0][:3, 3], rest[0][:3, 3])
        assert np.allclose(world[1][:3, 3], world[0][:3, 3] + x20 @ (rest[1][:3, 3] - rest[0][:3, 3]))
        assert np.allclose(world[2][:3, 3], world[1][:3, 3] + _turn((1, 0, 0), 30) @ (rest[2][:3, 3] - rest[1][:3, 3]))


def test_a_slot_lock_still_in_a_clip_leaves_the_chain_its_own_turns():
    spring = _carried_spring()
    held = _turn((1, 0, 0), 30)  # turned from rest, but the same in every frame
    poses = [_poses(np.eye(3), held, held)] * 3
    assert spring.carried(poses) == ([], [{}, {}, {}])
    own = [{0: spring.chains.hanging(0)}] * 3
    assert ma.carried_turns(spring, poses, own) is own
    barely = [_poses(np.eye(3), np.eye(3), np.eye(3)), _poses(np.eye(3), _turn((1, 0, 0), 1), np.eye(3))]
    assert spring.carried(barely)[0] == []  # less than SWING_MIN


class _Hair:
    """A stand-in for ``HairCheck``: the stretch is the largest turn of the chain's tip from rest (degrees / 10)."""

    own = 2.5

    def __init__(self):
        self.calls = 0

    def worst(self, spring, poses, turns):
        self.calls += 1
        return max(math.degrees(ms.angle_of(spring.posed(p, t)[2][:3, :3] @ spring.chains.rest[2][:3, :3].T)) / 10
                   for p, t in zip(poses, turns))


def test_carried_turns_replace_the_chains_own_and_are_scaled_down_or_dropped_when_the_hair_stretches():
    spring = _carried_spring()
    x20, x40 = _turn((1, 0, 0), 20), _turn((1, 0, 0), 40)
    poses = [_poses(np.eye(3), np.eye(3), np.eye(3))] + [_poses(np.eye(3), x20, x40)] * 2
    swung = spring.chains.hanging(0)
    swung[:3, :3] = _turn((0, 0, 1), 15)  # today's turn (the swing, then gravity) of the root
    own = [{0: swung, 5: np.eye(4)} for _ in poses]  # (5: a bone of another chain, kept)
    # within the costume's own stretch: the slot's turns, no gravity, the other chain's turns kept
    spring.hair = None
    merged = ma.carried_turns(spring, poses, own)
    assert sorted(merged[1]) == [0, 1, 2, 5] and np.allclose(merged[0][0], spring.chains.local[0])
    # past it at full turn (40 degrees at the tip): half the turn
    spring.hair = _Hair()
    halved = ma.carried_turns(spring, poses, own)
    tip = spring.chains.posed(np.eye(4), halved[1])[2][:3, :3] @ spring.chains.rest[2][:3, :3].T
    assert math.degrees(ms.angle_of(tip)) == pytest.approx(20.0) and spring.hair.calls == 2
    # past it at half as well: the chain's own turns
    spring.hair.own = 1.5
    assert ma.carried_turns(spring, poses, own) is own
