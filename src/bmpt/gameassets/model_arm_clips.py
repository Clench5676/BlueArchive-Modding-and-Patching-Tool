"""Lossless streamed arm-curve replacement and slot-local clip copies."""

from __future__ import annotations

import pickle
import struct
from collections import defaultdict, deque

import numpy as np

from . import model_skirts as ms
from .model import read_streamed, _key

ARM_TOLERANCE = 2e-5


def clone_tree(tree):
    """An independent copy of a type tree (plain containers and scalars, shared parts kept shared), equal to a deep
    copy and several times faster on clips with long curve arrays."""
    return pickle.loads(pickle.dumps(tree, pickle.HIGHEST_PROTOCOL))


def _layout(tree):
    data = tree["m_MuscleClip"]["m_Clip"]["data"]
    return data, data["m_StreamedClip"]["curveCount"], data["m_DenseClip"]["m_CurveCount"]


def _bindings(tree):
    index = 0
    for binding in tree["m_ClipBindingConstant"]["genericBindings"]:
        dims = ms.binding_dims(binding)
        yield index, dims, binding
        index += dims


def _arm(binding, rotations):
    return binding["typeID"] == 4 and binding["attribute"] in (2, 4) and binding["path"] in rotations


def encode_streamed(frames):
    data = bytearray()
    for time, keys in frames:
        data.extend(struct.pack("<fi", time, len(keys)))
        for key in keys:
            data.extend(struct.pack("<i4f", *key))
    return list(struct.unpack(f"<{len(data) // 4}I", data))


def encode_rotations(tree, rotations):
    """Replace six local arm rotations with per-frame quaternion streamed keys.

    Other streamed cubics, dense samples and constants are copied, only their
    indices move. Bindings, deltas, reference values and muscle size move with
    them. Euler arm bindings become quaternions. Missing hand curves are added.
    """
    data, ns, nd = _layout(tree)
    times = np.asarray(clip_times(tree), np.float32)
    if not len(times) or any(np.shape(v) != (len(times), 4) for v in rotations.values()):
        raise ValueError("arm samples and clip grid differ")
    entries = list(_bindings(tree))
    total = ns + nd + len(data["m_ConstantClip"]["data"])
    if sum(d for _, d, _ in entries) != total or tree.get("m_MuscleClipSize") != ms.muscle_size(tree):
        raise ValueError("clip layout not supported")
    groups = [[], [], []]
    for index, dims, binding in entries:
        group = 0 if index < ns else 1 if index < ns + nd else 2
        end = ns if group == 0 else ns + nd if group == 1 else total
        if index + dims > end:
            raise ValueError("binding crosses curve storage boundary")
        if not _arm(binding, rotations):
            groups[group].append((index, dims, binding))
    template = next((b for _, _, b in entries if b["typeID"] == 4), None)
    if template is None:
        raise ValueError("no transform binding")
    added = []
    for path, values in rotations.items():
        b = dict(template)
        b.update(path=path, attribute=2, typeID=4, customType=0, isPPtrCurve=0)
        if "script" in b:
            b["script"] = {"m_FileID": 0, "m_PathID": 0}
        added.append((path, b, np.asarray(values, np.float32)))
    mapping, generic = {}, []
    cursor = 0
    for old, dims, b in groups[0]:
        generic.append(b)
        mapping.update((old + k, cursor + k) for k in range(dims))
        cursor += dims
    arm_start = cursor
    generic.extend(b for _, b, _ in added)
    cursor += 4 * len(added)
    new_ns = cursor
    for group in groups[1:]:
        for old, dims, b in group:
            generic.append(b)
            mapping.update((old + k, cursor + k) for k in range(dims))
            cursor += dims
    new_nd = sum(d for _, d, _ in groups[1])
    frames = {}
    for t, keys in read_streamed(data["m_StreamedClip"]["data"]):
        frames.setdefault(t, []).extend((mapping[i], *coefficients) for i, *coefficients in keys if i in mapping)
    frames.setdefault(float("-inf"), [])
    frames.setdefault(float("inf"), [])
    for a, (_, _, values) in enumerate(added):
        for f, t in enumerate(times):
            slope = (values[f + 1] - values[f]) / (times[f + 1] - t) if f + 1 < len(times) else np.zeros(4)
            frames.setdefault(float(t), []).extend(
                (arm_start + 4 * a + k, 0.0, 0.0, float(slope[k]), float(values[f, k])) for k in range(4)
            )
        frames[float("-inf")].extend((arm_start + 4 * a + k, 0.0, 0.0, 0.0, float(values[0, k])) for k in range(4))
    data["m_StreamedClip"]["data"] = encode_streamed(
        (t, sorted(keys, key=lambda key: key[0])) for t, keys in sorted(frames.items())
    )
    data["m_StreamedClip"]["curveCount"] = new_ns
    dense = data["m_DenseClip"]
    old_dense = np.asarray(dense["m_SampleArray"]).reshape(dense["m_FrameCount"], nd)
    dense_columns = [i + k - ns for i, dims, _ in groups[1] for k in range(dims)]
    dense["m_SampleArray"] = old_dense[:, dense_columns].ravel().tolist()
    dense["m_CurveCount"] = new_nd
    constants = data["m_ConstantClip"]["data"]
    data["m_ConstantClip"]["data"] = [constants[i + k - ns - nd] for i, dims, _ in groups[2] for k in range(dims)]
    muscle = tree["m_MuscleClip"]
    deltas = muscle["m_ValueArrayDelta"]
    if len(deltas) != total:
        raise ValueError("clip value deltas and curves differ")
    moved = [None] * cursor
    for old, new in mapping.items():
        moved[new] = deltas[old]
    for a, (_, _, values) in enumerate(added):
        for k in range(4):
            moved[arm_start + 4 * a + k] = {"m_Start": float(values[0, k]), "m_Stop": float(values[-1, k])}
    muscle["m_ValueArrayDelta"] = moved
    reference = muscle["m_ValueArrayReferencePose"]
    if reference:
        if len(reference) != total:
            raise ValueError("clip reference pose and curves differ")
        moved_ref = [0.0] * cursor
        for old, new in mapping.items():
            moved_ref[new] = reference[old]
        for a, (_, _, values) in enumerate(added):
            moved_ref[arm_start + 4 * a : arm_start + 4 * a + 4] = values[0].tolist()
        muscle["m_ValueArrayReferencePose"] = moved_ref
    tree["m_ClipBindingConstant"]["genericBindings"] = generic
    tree["m_MuscleClipSize"] = ms.muscle_size(tree)


def edit_error(before, after, rotations):
    """Read-back check: non-arm curves value-for-value, arms within 20 ppm."""
    if clip_times(before) != clip_times(after):
        return "sample times changed"
    original = stored_curves(before)
    current = stored_curves(after)
    by_binding = defaultdict(deque)
    for i, dims, binding in _bindings(after):
        key = (binding["path"], binding["typeID"], binding["attribute"], binding.get("customType", 0))
        by_binding[key].append((i, dims))
    for index, dims, binding in _bindings(before):
        if _arm(binding, rotations):
            continue
        key = (binding["path"], binding["typeID"], binding["attribute"], binding.get("customType", 0))
        matches = by_binding.get(key)
        found = matches.popleft() if matches else None
        if found is None or found[1] != dims:
            return f"binding {key} missing"
        for k in range(dims):
            old, new = original.get(index + k), current.get(found[0] + k)
            same = old is new if old is None or new is None else np.array_equal(old, new, equal_nan=True)
            if not same:
                return f"curve {index + k} changed"
            delta0 = before["m_MuscleClip"]["m_ValueArrayDelta"][index + k]
            delta1 = after["m_MuscleClip"]["m_ValueArrayDelta"][found[0] + k]
            if not np.array_equal(
                [delta0["m_Start"], delta0["m_Stop"]], [delta1["m_Start"], delta1["m_Stop"]], equal_nan=True
            ):
                return f"curve {index + k} value delta changed"
    arm_indices = {
        matches[0][0] + k
        for path in rotations
        for matches in [by_binding.get((path, 4, 2, 0))]
        if matches
        for k in range(4)
    }
    _, sampled = sample_curves(after, arm_indices)
    for path, expected in rotations.items():
        matches = by_binding.get((path, 4, 2, 0))
        found = matches[0] if matches else None
        if found is None:
            return f"arm binding {path} missing"
        got = np.array([sampled.get(found[0] + k, []) for k in range(4)]).T
        if got.shape != expected.shape or not np.allclose(got, expected, atol=ARM_TOLERANCE, rtol=0):
            return f"arm {path} differs from solve"
    data, ns, nd = _layout(after)
    total = ns + nd + len(data["m_ConstantClip"]["data"])
    if len(after["m_MuscleClip"]["m_ValueArrayDelta"]) != total:
        return "value deltas differ from curves"
    if sum(d for _, d, _ in _bindings(after)) != total:
        return "bindings differ from curves"
    if after["m_MuscleClipSize"] != ms.muscle_size(after):
        return "muscle size differs"
    return None


def receivers(graph, skeleton, prefabs):
    """Only the slot's own override controllers and field clip lists."""
    found = {}
    for prefab in prefabs.values():
        animator = graph.component(prefab.deref(), "Animator")
        controller = graph.deref(animator, graph.tree(animator)["m_Controller"]) if animator is not None else None
        if controller is not None and controller.type.name == "AnimatorOverrideController":
            found[_key(controller)] = controller
    # Field prefabs may have separate behaviours for the same slot.
    from .model_adapt import Skeleton

    for prefab in prefabs.values():
        sk = skeleton if prefab.deref() is skeleton.nodes[0].go else Skeleton.of(graph, prefab.deref())
        for behaviour in sk.behaviours:
            if graph.script(behaviour) == "FieldAnimancerController":
                found[_key(behaviour)] = behaviour
    return list(found.values())


def repoint(tree, owner, copies, writer):
    """Repoint effective overrides, preserving base-controller and original refs."""
    changed = False
    if owner.type.name == "AnimatorOverrideController":
        for pair in tree["m_Clips"]:
            effective = pair["m_OverrideClip"] if pair["m_OverrideClip"].get("m_PathID") else pair["m_OriginalClip"]
            from .model_adapt import _cab

            replacement = copies.get((_cab(owner.assets_file, effective["m_FileID"]), effective["m_PathID"]))
            if replacement is not None:
                cab, pid = replacement
                pair["m_OverrideClip"] = {"m_FileID": writer.external(owner.assets_file, cab), "m_PathID": pid}
                changed = True
    else:
        from .model_adapt import _cab

        for i, ptr in enumerate(tree.get("animationClips", [])):
            replacement = copies.get((_cab(owner.assets_file, ptr["m_FileID"]), ptr["m_PathID"]))
            if replacement is not None:
                cab, pid = replacement
                tree["animationClips"][i] = {"m_FileID": writer.external(owner.assets_file, cab), "m_PathID": pid}
                changed = True
    return changed


def write_copies(writer, changes, destinations, holders, skeleton, clip_skirts, *, step=lambda: None):
    """Bake added curves into the same copies, verify serialized bytes, repoint.

    Retain the caller's preview/build class; yield between copies and sampled
    frames without adding locks or workers.
    """
    from . import model_adapt as ma

    if not changes:
        return {}
    referenced = set()
    for holder in holders:
        tree = holder.read_typetree()
        pointers = (
            [
                p["m_OverrideClip"] if p["m_OverrideClip"].get("m_PathID") else p["m_OriginalClip"]
                for p in tree["m_Clips"]
            ]
            if holder.type.name == "AnimatorOverrideController"
            else tree.get("animationClips", [])
        )
        referenced.update((ma._cab(holder.assets_file, p["m_FileID"]), p["m_PathID"]) for p in pointers)
    missing = set(changes) - referenced
    if missing:
        raise ma.refuse(
            "arm clip receiver not identified", clips=[changes[k].clip.peek_name() for k in sorted(missing)]
        )
    copies = {}
    for key, change in changes.items():
        step()
        tree = clone_tree(change.tree)
        before_arms = ma.clip_snapshot(tree)
        # the chains the clip's original would get (swinging roots, or hair carrying the slot's own lock keys)
        skirts = [s for s in clip_skirts.get(key, []) if s.solver.roots or getattr(s.solver, "carry", None) is not None]
        for skirt in skirts:
            # Cached hem probes sampled the uncorrected arms. Recompute added
            # chains on this corrected clip so their arm anchors follow it.
            skirt.solved.pop(key, None)
        prepared = ma.curve_frames(skeleton, skirts, tree) if skirts else None
        bones = [ma.skirt_curves(skeleton, s, tree, key, prepared=prepared, step=step) for s in skirts]
        if any(n is None for n in bones):
            raise ma.refuse("arm clip edit not verified", clip=change.clip.peek_name(), what="added curve layout")
        if sum(bones):
            problem = ma.clip_edit_error(before_arms, tree, 7 * sum(bones))
            if problem:
                raise ma.refuse("clip edit not verified", clip=change.clip.peek_name(), what=problem)
        cab = destinations[key]
        sf = writer.file(cab)
        # Event object references belong to the original clip's file.
        if cab != key[0]:
            for ptr in ma._pointers(tree):
                if ptr.get("m_PathID"):
                    ptr["m_FileID"] = writer.external(sf, ma._cab(change.clip.assets_file, ptr["m_FileID"]))
        pid = writer.add_like(sf, change.clip, tree)
        obj = sf.objects[pid]
        read_back = ma.saved_tree(obj)
        problem = edit_error(
            ma.saved_tree(change.clip) if change.clip.data else change.clip.read_typetree(), read_back, change.rotations
        )
        if problem:
            raise ma.refuse("arm clip edit not verified", clip=change.clip.peek_name(), what=problem)
        copies[key] = (cab, pid)
        for skirt in skirts:
            skirt.clips += 1
    for holder in holders:
        obj = writer.file(holder.assets_file.name.lower()).objects[holder.path_id]
        tree = ma.saved_tree(obj) if obj.data else obj.read_typetree()
        if repoint(tree, obj, copies, writer):
            obj.save_typetree(tree)
    # Keep the originals in preload ranges for timelines and other consumers,
    # and load each copy wherever a slot asset previously loaded its original.
    for _, sf in writer.files.values():
        for obj in list(sf.objects.values()):
            if obj.type.name != "AssetBundle":
                continue
            tree = ma.saved_tree(obj) if obj.data else obj.read_typetree()
            additions = {}
            for entry, (_, info) in enumerate(tree["m_Container"]):
                begin, count = info["preloadIndex"], info["preloadSize"]
                extra = []
                for ptr in tree["m_PreloadTable"][begin : begin + count]:
                    replacement = copies.get((ma._cab(sf, ptr["m_FileID"]), ptr["m_PathID"]))
                    if replacement is not None:
                        cab, pid = replacement
                        extra.append({"m_FileID": writer.external(sf, cab), "m_PathID": pid})
                if extra:
                    additions[entry] = list({(p["m_FileID"], p["m_PathID"]): p for p in extra}.values())
            if additions:
                ma._extend_preload(tree, additions)
                obj.save_typetree(tree)
    return copies


def clip_times(tree):
    muscle = tree["m_MuscleClip"]
    start, stop = muscle["m_StartTime"], muscle["m_StopTime"]
    rate = tree.get("m_SampleRate") or 30.0
    return [start + f / rate for f in range(max(1, round((stop - start) * rate) + 1))]


_KEY = np.dtype([("index", "<i4"), ("coefs", "<f4", (4,))])


def _streamed_keys(words):
    """Every key of a streamed clip in stream order: its frame's time (float64), curve index and four cubic
    coefficients (float32). Frames are walked by their key counts; the keys are then read in one gather."""
    w = np.asarray(words, dtype="<u4")
    starts, counts, pos, end = [], [], 0, len(w)
    while pos + 2 <= end:
        count = int(w[pos + 1])
        count -= (count >> 31) << 32
        if count < 0 or pos + 2 + 5 * count > end:
            raise ValueError("streamed clip data is truncated")
        starts.append(pos)
        counts.append(count)
        pos += 2 + 5 * count
    starts, counts = np.array(starts, int), np.array(counts, int)
    first = np.repeat(starts + 2, counts) + 5 * (
        np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    )
    times = np.repeat(w[starts].view("<f4").astype(float), counts)
    index = w[first].view("<i4")
    coefs = w[first[:, None] + np.arange(1, 5)].view("<f4")
    return times, index, coefs


def sample_curves(tree, wanted, *, hold_before=False):
    """Sample selected scalar curves with the runtime decoder's cubic semantics."""
    times = np.array(clip_times(tree))
    data = tree["m_MuscleClip"]["m_Clip"]["data"]
    streamed, dense = data["m_StreamedClip"], data["m_DenseClip"]
    selected = np.zeros(streamed["curveCount"], bool)
    for k in wanted:
        if k < len(selected):
            selected[k] = True
    key_at, index, coefs = _streamed_keys(streamed["data"])
    inside = (key_at > -1e30) & (key_at < 1e30)
    keep = np.zeros(len(index), bool)
    keep[inside] = selected[index[inside]]
    curves = {}
    if inside.any():
        rows = np.zeros(int(keep.sum()), _KEY)
        rows["index"], rows["coefs"] = index[keep], coefs[keep]
        key_at = key_at[keep]
        order = np.argsort(rows["index"], kind="stable")
        rows, key_at = rows[order], key_at[order]
        cuts = np.r_[0, np.flatnonzero(np.diff(rows["index"])) + 1, len(rows)]
        for begin, end in zip(cuts[:-1], cuts[1:]):
            if end == begin:
                continue
            ks = key_at[begin:end]
            at = np.maximum(0, np.searchsorted(ks, times + 1e-6, side="right") - 1)
            chosen = rows["coefs"][begin:end][at].astype(float)
            dt = np.maximum(0, times - ks[at])
            values = ((chosen[:, 0] * dt + chosen[:, 1]) * dt + chosen[:, 2]) * dt + chosen[:, 3]
            if hold_before:
                # The scalar decoder holds c3 directly before the first key,
                # preserving even its signed zero and non-finite coefficients.
                values = np.where(times < ks[at], chosen[:, 3], values)
            curves[int(rows["index"][begin])] = values
    ns, nd = streamed["curveCount"], dense["m_CurveCount"]
    if nd and dense["m_FrameCount"]:
        samples = np.asarray(dense["m_SampleArray"]).reshape(dense["m_FrameCount"], nd)
        f = np.clip((times - dense["m_BeginTime"]) * dense["m_SampleRate"], 0, len(samples) - 1)
        first = f.astype(int)
        last, alpha = np.minimum(first + 1, len(samples) - 1), f - first
        for k in wanted:
            if ns <= k < ns + nd:
                curves[k] = samples[first, k - ns] * (1 - alpha) + samples[last, k - ns] * alpha
    for i, value in enumerate(data["m_ConstantClip"]["data"]):
        if ns + nd + i in wanted:
            curves[ns + nd + i] = np.full(len(times), value)
    return times, curves


def _streamed_records(words):
    """Per streamed curve its keys in stream order as rows (time, c0, c1, c2, c3), helper frames included: the
    values ``read_streamed`` gives, read as arrays."""
    key_at, index, coefs = _streamed_keys(words)
    if not len(index):
        return {}
    order = np.argsort(index, kind="stable")
    index = index[order]
    rows = np.column_stack((key_at, coefs.astype(float)))[order]
    cuts = np.flatnonzero(np.diff(index)) + 1
    return {int(group[0]): values for group, values in zip(np.split(index, cuts), np.split(rows, cuts))}


def stored_curves(tree):
    """Lossless signatures for every unedited curve, independent of its index.

    Streamed records include helper keys, duplicate-key order and every cubic
    coefficient; dense signatures include their time grid. Equality is stronger
    than sampling every frame and costs only the stored keys, not all curves
    times all frames. Added dense curves cannot mask a changed original key.
    """
    data, ns, nd = _layout(tree)
    out = _streamed_records(data["m_StreamedClip"]["data"])
    dense = data["m_DenseClip"]
    if nd:
        samples = np.array(dense["m_SampleArray"]).reshape(dense["m_FrameCount"], nd)
        grid = [dense["m_BeginTime"], dense["m_SampleRate"], dense["m_FrameCount"]]
        for k in range(nd):
            out[ns + k] = np.r_[grid, samples[:, k]]
    for k, value in enumerate(data["m_ConstantClip"]["data"]):
        out[ns + nd + k] = np.array([value])
    return out
