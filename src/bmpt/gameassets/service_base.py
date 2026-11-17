"""What the game asset service and its 3D model part share: the cache schema, the model lock, lobby effects."""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..core import preview_gate, spans
from . import lobby
from .unity import reference


logger = logging.getLogger("bmpt.gameassets.service")  # the game asset service logs under one name

_SCHEMA = "gameassets-v1/"


class _ModelLock:
    """The model lock: one reader at a time of those that hold several bundles open at once.

    Those readers are model and clip exports, the selection-frame layout, lobby particles and office models, each
    keeping the path lock of every bundle it read until it ends, so two of them could wait on each other's bundles. Held
    for one export (about 1 s for a model, 0.5 s for a clip, no step inside it); taken after the library's lock, never
    before it. A costume build needs no model lock: it reads bundles from memory after each individually locked load
    (``unity.load_bundle``). A foreground preview that waits for it while a job or background work holds it (the costume
    check's export, a lobby warm-up) waits outside its own time (``preview_gate.waiting``): the page's deadline leaves
    the wait out.
    """

    def __init__(self) -> None:
        """Create an unlocked export lock with no background holder."""
        self._lock = threading.Lock()
        self._job = False  # the holder answers no foreground preview

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the export lock, excluding background-holder waits from foreground deadlines.

        Args:
            blocking: Whether to wait when another export holds the lock.
            timeout: Maximum wait in seconds for a blocking acquire; -1 waits indefinitely.

        Returns:
            Whether the lock was acquired.
        """
        if not self._lock.acquire(False):
            if not blocking:
                return False
            if self._job and preview_gate.in_foreground():
                with preview_gate.waiting():
                    if not self._lock.acquire(True, timeout):
                        return False
            elif not self._lock.acquire(True, timeout):
                return False
        self._job = not preview_gate.in_foreground()
        return True

    def release(self) -> None:
        """Release the held export lock."""
        self._lock.release()

    def locked(self) -> bool:
        """Report whether an export currently holds the lock."""
        return self._lock.locked()

    def __enter__(self) -> bool:
        """Acquire the export lock for the context."""
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        """Release the export lock when the context exits."""
        self.release()


_MODEL_LOCK = _ModelLock()


def model_lock(what: str) -> Any:
    """Wrap the model lock with wait/hold spans named model-lock.<what> for the supplied operation label."""
    return spans.held(f"model-lock.{what}", _MODEL_LOCK, hold=True)


# Unity's built-in meshes live in "Library/unity default resources", outside the bundles; the lobbies' effect quads
# use its Quad
_BUILTIN_QUAD = ("library/unity default resources", 10210)


def _builtin_quad(owner: Any, pointer: dict | None) -> tuple[str, None] | None:
    """("quad", None) for a pointer to Unity's built-in Quad mesh, else None."""
    fid, pid = (pointer or {}).get("m_FileID", 0), (pointer or {}).get("m_PathID", 0)
    try:
        path = owner.assets_file.externals[fid - 1].path if owner is not None and fid > 0 else ""
    except (AttributeError, IndexError):
        return None
    return ("quad", None) if (str(path).lower(), pid) == _BUILTIN_QUAD else None


class _FxResolver:
    """Reads what a lobby's renderers point at, for lobby.read_prefab.

    A particle material is read as (id, particles.material) with its textures (particles.texture), a mesh as (id,
    particles.mesh) (Unity's built-in Quad as ("quad", None)), the render queue of a skeleton's atlas pages ("pages": a
    SkeletonDataAsset, or one Material) as (id, {texture name: queue}), or their shaders' names ("shaders") as (id,
    {texture name: shader name}), the materials a skeleton is drawn with ("skeleton": a SkeletonDataAsset) as (id,
    spine_materials.skeleton) and an Animator's controller ("animator") as (id, {"decoded", "bindings", "loop"}) of its
    default state's clip. Ids are "<cab>_<path id>".
    """

    def __init__(self, graph: Any, objects: dict[int, Any], shader_forms: dict):
        """Prepare dependency reads for one prefab's effect components.

        Args:
            graph: BundleGraph resolving external pointers and caching typetrees.
            objects: Prefab-file readers indexed by path ID, identifying each pointer owner.
            shader_forms: Shared parsed-shader cache indexed by CAB and path ID.
        """
        self.graph, self.objects, self.forms = graph, objects, shader_forms
        self.done: dict[tuple[str, str], Any] = {}

    def resolve(self, kind: str, owner: int, pointer: dict) -> tuple[str, Any] | None:
        """Resolve an effect pointer to a stable ID and player-ready data, caching failures too.

        Args:
            kind: material, mesh, pages, shaders, skeleton, animator, timeline, or sprite description to read.
            owner: Prefab-file component path ID supplying the pointer's external-file table.
            pointer: Unity m_FileID/m_PathID reference to the requested asset.

        Returns:
            (<lower-case CAB>_<path ID>, description), ("quad", None) for the built-in Quad,
            or None for an absent, incompatible, or unreadable object.
        """
        from . import particles
        from .model_glb import read_mesh

        g = self.graph
        own = self.objects.get(owner)
        obj = g.deref(own, pointer) if own is not None else None
        if obj is None:
            return _builtin_quad(own, pointer) if kind == "mesh" else None
        oid = f"{obj.assets_file.name.lower()}_{obj.path_id}"
        if (kind, oid) in self.done:
            return self.done[(kind, oid)]
        out = None
        try:
            if kind == "material" and obj.type.name == "Material":
                out = (oid, self._material(obj))
            elif kind == "mesh" and obj.type.name == "Mesh":
                data = read_mesh(obj.read())
                tris = [t for sub in data.triangles for t in sub]
                out = (oid, particles.mesh(data.vertices, data.uv0, data.normals, tris, data.colors))
            elif kind in ("pages", "shaders") and obj.type.name in ("MonoBehaviour", "Material"):
                out = (oid, self._pages(obj, kind))
            elif kind == "skeleton" and obj.type.name == "MonoBehaviour":
                out = (oid, self._skeleton(obj))
            elif kind == "animator" and obj.type.name == "AnimatorController":
                out = self._animator(obj, oid)
            elif kind == "timeline" and obj.type.name == "MonoBehaviour":
                from . import intro

                out = (oid, intro.read_timeline(g, obj, sound=self._sound))
            elif kind == "sprite" and obj.type.name == "Sprite":
                out = (oid, self._sprite(obj))
        except Exception:  # an unreadable object: the system is left out
            logger.debug("Cannot read lobby particle %s %s", kind, oid, exc_info=True)
            out = None
        self.done[(kind, oid)] = out
        return out

    def _sound(self, asset: Any, data: dict) -> dict | None:
        """A TimeLineAudioController's AudioData as a sound event (lobby.sound_event), with its id: the asset's."""

        def clip_id(pointer: dict) -> str | None:
            if not pointer.get("m_PathID"):
                return None
            cab, pid = reference(asset, pointer)
            return f"{cab.lower()}_{pid}"

        event = lobby.sound_event({"m_Name": "Intro", "AudioData": data}, clip_id)
        return {**event, "id": f"{asset.assets_file.name.lower()}_{asset.path_id}"} if event else None

    def _sprite(self, obj: Any) -> dict | None:
        """A Sprite as intro.sprite draws it.

        That is its texture (particles.texture), its rect in the texture's pixels, its size in units (rect / pixels per
        unit) and its pivot (0 to 1). The pixels are read when served.
        """
        from . import particles

        g = self.graph
        st = g.tree(obj)
        rd = st.get("m_RD") or {}
        tex = g.deref(obj, rd.get("texture"))
        if tex is None or tex.type.name != "Texture2D":
            return None
        tex_id = f"{tex.assets_file.name.lower()}_{tex.path_id}"
        info = particles.texture(g.tree(tex), tex_id)
        g.trees.pop((tex.assets_file.name.lower(), tex.path_id), None)
        r, tr = st.get("m_Rect") or {}, rd.get("textureRect") or {}
        ppu = float(st.get("m_PixelsToUnits") or 100.0) or 100.0
        pivot = st.get("m_Pivot") or {}
        return {
            "tex": info,
            "rect": [float(tr.get(k, 0.0)) for k in ("x", "y", "width", "height")],
            "size": [float(r.get("width", 0.0)) / ppu, float(r.get("height", 0.0)) / ppu],
            "pivot": [float(pivot.get("x", 0.5)), float(pivot.get("y", 0.5))],
        }

    def _pages(self, obj: Any, kind: str = "pages") -> dict[str, Any]:
        """{texture name: render queue} of a SkeletonDataAsset's atlas pages, as spine-unity draws them.

        Each page comes with the material its SpineAtlasAsset lists for it (matched by the material's main texture name,
        which is the page's file name without its extension). For a Material, its own page. With kind "shaders",
        {texture name: the name of the material's shader} instead (a page without a shader is left out). The textures'
        pixels are never read.
        """
        g = self.graph
        materials = [obj]
        if obj.type.name == "MonoBehaviour":
            materials = []
            for atlas in (g.deref(obj, p) for p in g.tree(obj).get("atlasAssets") or []):
                if atlas is not None:
                    materials += [g.deref(atlas, p) for p in g.tree(atlas).get("materials") or []]
        out: dict[str, Any] = {}
        for mat in materials:
            if mat is None or mat.type.name != "Material":
                continue
            tree = g.tree(mat)
            envs = dict((tree.get("m_SavedProperties") or {}).get("m_TexEnvs", []))
            tex = g.deref(mat, (envs.get("_MainTex") or {}).get("m_Texture"))
            name = tex.peek_name() if tex is not None else None
            if name:
                shader, _, state = self._state(mat, tree)
                value = shader if kind == "shaders" else state["queue"]
                if value is not None:
                    out.setdefault(name, value)
        return out

    def _state(self, obj: Any, tree: dict) -> tuple[str | None, dict | None, dict]:
        """A material's shader name, the shader's parsed form and the render state of its colour pass.

        The parsed form holds the parts read here; the render state is this material's (model.shader_state: blend and
        queue).
        """
        from .model import shader_state

        g = self.graph
        shader = g.deref(obj, tree.get("m_Shader"))
        name, form = None, None
        if shader is not None:
            key = (shader.assets_file.name.lower(), shader.path_id)
            if key not in self.forms:
                # a shader's typetree carries its compiled programs: read once, keep the parts used here
                full = g.tree(shader).get("m_ParsedForm") or {}
                self.forms[key] = {
                    "m_Name": full.get("m_Name"),
                    "m_PropInfo": full.get("m_PropInfo", {"m_Props": []}),
                    "m_SubShaders": [
                        {
                            "m_Tags": s.get("m_Tags"),
                            "m_Passes": [{"m_State": p.get("m_State")} for p in s.get("m_Passes", [])],
                        }
                        for s in full.get("m_SubShaders", [])
                    ],
                }
                g.trees.pop((key[0], key[1]), None)
            form = self.forms[key]
            name = form.get("m_Name")
        props = tree.get("m_SavedProperties") or {}
        floats = {k: v for k, v in props.get("m_Floats", []) if isinstance(v, (int, float))}
        try:
            state = shader_state(form, floats, tree.get("m_CustomRenderQueue", -1)) if form else {}
        except (KeyError, TypeError, IndexError, ValueError):
            state = {}
        if not state:
            queue = tree.get("m_CustomRenderQueue", -1)
            state = {"src": 1, "dst": 0, "queue": queue if queue and queue >= 0 else 3000}
        return name, form, state

    def _material(self, obj: Any) -> dict:
        from . import particles

        g = self.graph
        tree = g.tree(obj)
        name, form, state = self._state(obj, tree)
        props = tree.get("m_SavedProperties") or {}
        textures = {}
        for prop, env in props.get("m_TexEnvs", []):
            tex = g.deref(obj, env.get("m_Texture"))
            if tex is not None and tex.type.name == "Texture2D":
                textures[prop] = particles.texture(g.tree(tex), f"{tex.assets_file.name.lower()}_{tex.path_id}")
                g.trees.pop((tex.assets_file.name.lower(), tex.path_id), None)  # its pixels are read when served
        out = particles.material(name, tree, state, form["m_PropInfo"].get("m_Props", []) if form else (), textures)
        out["cull"] = state.get("cull", 2)  # back faces are culled (2), front faces (1) or none (0): meshfx.py's meshes
        return out

    def _skeleton(self, obj: Any) -> dict:
        """The materials of a SkeletonDataAsset's atlas pages and its blend mode replacement materials.

        See spine_materials.skeleton. Only the pages a ported shader draws read their other textures' settings; no
        pixels are read.
        """
        from . import spine_materials

        g = self.graph
        tree = g.tree(obj)
        mats = []
        for atlas in (g.deref(obj, p) for p in tree.get("atlasAssets") or []):
            if atlas is not None:
                for mat in (g.deref(atlas, p) for p in g.tree(atlas).get("materials") or []):
                    if mat is not None and mat.type.name == "Material":
                        mats.append(self._spine_material(mat))

        def replacement(pointer: dict) -> tuple[str | None, dict] | None:
            mat = g.deref(obj, pointer)
            return self._spine_material(mat) if mat is not None and mat.type.name == "Material" else None

        return spine_materials.skeleton(
            mats, tree.get("blendModeMaterials") or {}, replacement, tree.get("scale") or 0.01
        )

    def _spine_material(self, mat: Any) -> tuple[str | None, dict]:
        from . import particles, spine_materials

        g = self.graph
        tree = g.tree(mat)
        name, form, _ = self._state(mat, tree)
        envs = dict((tree.get("m_SavedProperties") or {}).get("m_TexEnvs", []))
        main = g.deref(mat, (envs.get("_MainTex") or {}).get("m_Texture"))
        textures = {}
        for prop, env in envs.items() if spine_materials.program(name) else ():
            tex = g.deref(mat, env.get("m_Texture")) if prop != "_MainTex" else None
            if tex is not None and tex.type.name == "Texture2D":
                textures[prop] = particles.texture(g.tree(tex), f"{tex.assets_file.name.lower()}_{tex.path_id}")
                g.trees.pop((tex.assets_file.name.lower(), tex.path_id), None)
        return (main.peek_name() if main is not None else None), spine_materials.material(name, form, tree, textures)

    def _animator(self, controller: Any, oid: str) -> tuple[str, dict] | None:
        """The clip an AnimatorController's first layer plays in its default state.

        The lobbies' controllers have one state, looping one clip. The clip is decoded (model.decode_clip) with its
        bindings.
        """
        from .model_glb import decode_clip

        g = self.graph
        tree = g.tree(controller)
        clips = [g.deref(controller, p) for p in tree.get("m_AnimationClips") or []]
        machine = ((tree.get("m_Controller") or {}).get("m_StateMachineArray") or [{}])[0]
        machine = machine.get("data", machine)
        states = machine.get("m_StateConstantArray") or []
        clip = None
        if states:
            state = states[min(int(machine.get("m_DefaultState", 0)), len(states) - 1)]
            state = state.get("data", state)
            for blend in state.get("m_BlendTreeConstantArray") or []:
                for node in blend.get("data", blend).get("m_NodeArray") or []:
                    node = node.get("data", node)
                    index = node.get("m_ClipID", -1)
                    if 0 <= index < len(clips) and clips[index] is not None:
                        clip = clips[index]
                        break
                if clip is not None:
                    break
        if clip is None or clip.type.name != "AnimationClip":
            return None
        ct = g.tree(clip)
        bindings = (ct.get("m_ClipBindingConstant") or {}).get("genericBindings", [])
        loop = bool((ct.get("m_MuscleClip") or {}).get("m_LoopTime", True))
        return oid, {"decoded": decode_clip(ct), "bindings": bindings, "loop": loop}
