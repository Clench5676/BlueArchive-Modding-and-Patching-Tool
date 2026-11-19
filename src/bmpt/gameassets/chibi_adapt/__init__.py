"""Put another alt's costume on a student's 3D chibi slot.

A student's chibi slot (the ``character-<dev>-_mxload-*`` prefabs and their own dependency bundles) gets the costume of
another chibi: its skinned costume meshes with bindposes rebound onto the slot's skeleton, its materials and its
textures, all added as new objects to the slot's own bundles; the slot's prefabs then draw them in place of their own
costume. What exists changes only where the slot's prefabs need it (their renderers, transforms, behaviours and preload
table) and, with Rule B, in the slot's own clips, which gain curves for the new skirt bones and retain every curve
(read back value for value, :func:`clip_edit_error`): field, cutscene and other alts' bundles reference the slot's
objects, and a prefab of theirs that plays such a clip has no node at the new curves' paths, which Unity leaves unbound.

Everything is decided from data, never from names a character's artist chose, so new characters need no rules of their
own:

- renderer roles (:func:`roles`): shading of the materials, membership of the main ``Bip001`` rig, visibility at rest
  and in the idle clip;
- bone mapping (:func:`map_bones`): Biped bones (``Bip001 ...``, the names every clip and IK component uses) by name;
  every other bone by structure: the nearest slot bone hanging off the same Biped bone, in the chain
  its parent maps to, else its parent's; never a bone the slot's clips hide by scaling it to nothing. A missing
  Biped bone, or a bone outside the Biped, follows the Biped bone that moves it alike under the source's own clips
  (:func:`carriers`: a Battle NPC's ForeTwist with the forearm, a bag's strap with the body). Per Biped bone, the group
  of secondary bones follows the matched bones only while it stretches no more than the slot's own costume under the
  slot's clips, else it stays rigid;
- the shape (:func:`plan`): the costume as its vertex data stores it, as its prefab draws it at rest (by its own
  bindposes: some meshes lie a metre off their renderer), and refitted to the slot's joints (:func:`refit`), each
  judged under the slot's clips; the least stretch wins and its vertices, normals and tangents are written
  (``chibi_geometry``);
- checks: every reference resolved, the rest pose reproduced, the stretch within the slot's own range. A pair that fails
  raises ``source_unsupported``;
- the hem (:func:`hems`, rules in ``chibi_hems``): cloth around the legs that stays rigid gets new skin weights on the
  pelvis, thighs and calves, or (Rule B, ``chibi_skirts``) its skirt chains become new bones of the slot with rotation
  curves in the slot's clips, by the rule that keeps the legs inside best under the slot's own clips within the slot's
  own stretch range; rigid weights stay where no rule does better. Optimize Physics (``physics``) also judges the
  leg-anchor groups set to ``follow`` again: the rules are built as if they were rigid too and must beat the follow
  result by the same measure;
- the mouth (:func:`mouth_of`): the slot's mouth receivers index the renderer's replacement materials, which are the
  source's, so they take the source receiver's material index and default tile, and point at the part added for the
  source's mouth where that is not on the primary. The slot's clips name mouth tiles as cells of the slot's own mouth
  atlas: a source eye-mouth material drawing another atlas (another texture, scale or grid: Hina's 4 x 4 atlas, the
  shared 8 x 8 one) takes the slot's, and the receivers keep the slot's default tile (:func:`retiled_mouths`);
- the halo (:func:`halo_of`): after any swap the halo is the source's or none, never the slot's own
  (``adapt``'s ``halo``). The source's halo mesh, materials and textures are added like the costume's and the slot's
  halo renderer draws them where the source draws its own: a followed halo (one unskinned renderer below an
  ``FxFollower``) with its follower offset taken from the source, a halo skinned to one bone below the head (NPC asset
  chibis such as Ayame's) at that bone's rest place, the follower set to keep it there. A followed halo of any other
  shape (particles: Tsurugi's, Mika's), or one going onto such a slot, is carried with its nodes: what the slot's halo
  draws is hidden and the source's nodes below its followed node become new nodes below the slot's, with their
  renderers and particle systems (:func:`_halo_transplant`). Every other case (a source without a halo, a boss's
  halo, anything below the followed node that points elsewhere) hides everything the slot's halo draws.

- set pieces (``chibi_setpieces``): what a costume mesh holds beside clothing, a seat the chibi sits in or a prop
  her clips bring out at times, belongs to the slot's animation. The source's draw nothing on the slot; the slot's
  own are drawn again by a renderer of their own, on the slot's bones.

The caller supplies official copies of the files (``adapt``'s ``index``) and saves the changed bundles.

The modules: ``skeleton`` (the shared limits and the skeleton), ``parts`` (renderer roles, clips, meshes),
``costume`` (bone mapping and the fitted shape), ``skirts`` (skirts, hems, springs), ``face`` (face bones, the clip
check), ``halo``, ``writing`` (new objects, sanity checks, the mouth) and ``transplant`` (``adapt`` itself).
"""

from .. import chibi_skirts as ms
from ..chibi import BundleGraph, _key
from ..chibi_glb import UNITY_ORDER, bind_tracks, euler_orders, euler_to_quat
from .costume import (
    Costume,
    Flat,
    Plan,
    _shoulder_mount,
    carriers,
    drawn,
    face_bone,
    fit_anchor,
    flatten,
    hidden_bones,
    map_bones,
    plan,
    refit,
    scaled_away,
)
from .face import clip_edit_error, clip_snapshot, saved_tree
from .halo import FOLLOWER_OFFSETS, _halo_change, _halo_shift, _mesh_tree, _pointers, halo_of
from .parts import Part, Skin, _shader_kind, bindposes, deform, pick, prefab_clips, probe_poses, roles, skin_of, stretch
from .skeleton import (
    BINDPOSE_LIMIT,
    BIPED_CHAIN,
    HEAD,
    IDLE_CLIPS,
    VERTEX_LIMIT,
    WEIGHT_TOLERANCE,
    Node,
    Skeleton,
    _inverse,
    refuse,
    stretch_limit,
    trs,
)
from .skirts import (
    FIT_KEYS,
    REGROUP,
    THIN_GAIN,
    FaceRetarget,
    Skirt,
    _rotation,
    _skirt_candidates,
    curve_frames,
    followed_groups,
    legs_of,
    skirt_curves,
    skirt_sequence,
)
from .transplant import (
    COSTUME_PHASES,
    Result,
    _append_beside,
    _cab,
    _extend_preload,
    _chibi_prefabs,
    adapt,
    choose_prefab,
    costume_weights,
    own_prefabs,
)
from .writing import CARRIED_SHADER, Rendered, Written, _sanity, mouth_atlas, mouth_of, retiled_mouths

__all__ = [
    "_append_beside",
    "_cab",
    "_extend_preload",
    "_halo_change",
    "_halo_shift",
    "_inverse",
    "_key",
    "_mesh_tree",
    "_chibi_prefabs",
    "_pointers",
    "_rotation",
    "_sanity",
    "_shader_kind",
    "_shoulder_mount",
    "_skirt_candidates",
    "adapt",
    "bind_tracks",
    "BINDPOSE_LIMIT",
    "bindposes",
    "BIPED_CHAIN",
    "BundleGraph",
    "CARRIED_SHADER",
    "carriers",
    "choose_prefab",
    "clip_edit_error",
    "clip_snapshot",
    "Costume",
    "COSTUME_PHASES",
    "costume_weights",
    "curve_frames",
    "deform",
    "drawn",
    "euler_orders",
    "euler_to_quat",
    "face_bone",
    "FaceRetarget",
    "fit_anchor",
    "FIT_KEYS",
    "Flat",
    "flatten",
    "followed_groups",
    "FOLLOWER_OFFSETS",
    "halo_of",
    "HEAD",
    "hidden_bones",
    "IDLE_CLIPS",
    "legs_of",
    "map_bones",
    "mouth_atlas",
    "mouth_of",
    "ms",
    "Node",
    "own_prefabs",
    "Part",
    "pick",
    "Plan",
    "plan",
    "prefab_clips",
    "probe_poses",
    "refit",
    "refuse",
    "REGROUP",
    "Rendered",
    "Result",
    "retiled_mouths",
    "roles",
    "saved_tree",
    "scaled_away",
    "Skeleton",
    "Skin",
    "skin_of",
    "Skirt",
    "skirt_curves",
    "skirt_sequence",
    "stretch",
    "stretch_limit",
    "THIN_GAIN",
    "trs",
    "UNITY_ORDER",
    "VERTEX_LIMIT",
    "WEIGHT_TOLERANCE",
    "Written",
]
