"""Describe memorial lobby materials as spine-unity sets them, for web/js/spine/skeleton-materials.js.

spine-unity draws every submesh with the material of its atlas page (the SpineAtlasAsset's materials, matched by their
main texture). Most pages use the plain spine shaders, which the web UI's spine-player reproduces; some use other
shaders: Aru's third page multiplies (Spine/Blend Modes/Skeleton PMA Multiply), water and river pages of three lobbies
scroll a noise distortion (Spine/Skeleton MX Standard - NoiseFlowAnimation) and one game screen glitches
(Spine/Skeleton MX Portrait Noise). Those pages are described here ("pages"), by the page's file name without its
extension, with the shader's blend and the material's own values; the plain pages are left out.

A slot's blend mode reaches the screen only through materials: with SkeletonDataAsset.blendModeMaterials
.requiresBlendModeMaterials set, BlendModeMaterials.ApplyMaterials gives the attachments of Multiply and Screen slots
(and Additive ones with applyAdditiveMaterial) a copy of their page with the replacement material listed for that page;
without it, or without a list for the mode, they keep their page's material and draw as Normal (ch0163's light_01,
Hiyori's R_eye_ligth_02, airi0's FX_Light_01). A page the list does not name gets no material, as a null reference.
An Additive slot always adds: the mesh generator writes its premultiplied vertex colour with
alpha 0 (pmaVertexColors). "blend" lists the modes that have replacement materials, by page; a replacement that is not
set (a null reference) is None.

The renderer never writes the dark colour (SkeletonRenderer.tintBlack is off in every lobby prefab), and none of these
shaders reads it: "tintBlack" says whether it applies.

Everything here works on plain typetrees and material descriptions, so it runs without game files; the service reads
the bundles.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

# the shaders the web UI ports (skeleton-materials.js PROGRAMS), by ShaderLab name; the others draw as spine-player's
# own (Spine/Skeleton, Spine/Skeleton MX Standard: the texture premultiplied, times the vertex colour, One
# OneMinusSrcAlpha)
PROGRAMS = {
    "Spine/Blend Modes/Skeleton PMA Multiply": "pma",
    "Spine/Blend Modes/Skeleton PMA Screen": "pma",
    "Spine/Blend Modes/Skeleton PMA Additive": "pma",
    "Spine/Skeleton MX Standard - NoiseFlowAnimation": "noiseFlow",
    "Spine/Skeleton MX Portrait Noise": "portraitNoise",
}
# the replacement material lists of BlendModeMaterials, by the slot blend mode they serve
_BLEND_LISTS = (("multiply", "multiplyMaterials"), ("screen", "screenMaterials"), ("additive", "additiveMaterials"))


def program(shader: str | None) -> str | None:
    """Return the page shader's web program, or None when spine-player already reproduces it."""
    return PROGRAMS.get(shader or "")


def page_name(name: str | None) -> str | None:
    """Return an atlas page filename without its extension, preserving None."""
    return name.rsplit(".", 1)[0] if name and "." in name else name


def blend_state(form: Mapping[str, Any] | None, floats: Mapping[str, float]) -> dict[str, int]:
    """Read Unity color and alpha blend factors from the first shader pass.

    Factors use UnityEngine.Rendering.BlendMode. Each names a material property (_SrcBlend, ...)
    or a fixed pass value. The "<noninit>" marker ignores stray material floats, such as Hina's multiply page's.

    Args:
        form: Parsed shader form; None or an unreadable pass uses premultiplied-alpha defaults.
        floats: Material float properties used only when the pass explicitly references them.
    """

    def value(entry: Mapping[str, Any], default: int) -> int:
        name = entry.get("name", "")
        if name and name != "<noninit>" and name in floats:
            return int(floats[name])
        return int(entry.get("val", default))

    try:
        state = form["m_SubShaders"][0]["m_Passes"][0]["m_State"]
        blend = state.get("rtBlend0") or state["rtBlend"][0]
        return {
            "src": value(blend["srcBlend"], 1),
            "dst": value(blend["destBlend"], 10),
            "srcA": value(blend["srcBlendAlpha"], 1),
            "dstA": value(blend["destBlendAlpha"], 10),
        }
    except (KeyError, TypeError, IndexError, ValueError):
        return {"src": 1, "dst": 10, "srcA": 1, "dstA": 10}


def material(
    shader: str | None, form: Mapping[str, Any] | None, tree: Mapping[str, Any], textures: Mapping[str, dict]
) -> dict:
    """Describe a page or replacement material's program, blend, properties, and textures.

    _STRAIGHT_ALPHA_INPUT makes the shader premultiply the texture. Float and Color values
    remain as stored, filled from shader defaults when absent; the front end converts Colors
    to linear light. Textures exclude the page's own, with empty slots naming shader defaults.

    Args:
        shader: ShaderLab name selecting the web program, or None for an unresolved shader.
        form: Parsed shader form supplying pass state and default properties; None uses material values alone.
        tree: Material typetree supplying saved Float/Color properties and shader keywords.
        textures: Texture properties mapped to particles.texture descriptions, excluding unresolved slots.
    """
    saved = tree.get("m_SavedProperties") or {}
    floats = {k: round(float(v), 6) + 0.0 for k, v in saved.get("m_Floats", []) if isinstance(v, (int, float))}
    colors = {k: [round(float(v.get(c, 0.0)), 6) + 0.0 for c in "rgba"] for k, v in saved.get("m_Colors", [])}
    defaults = {}
    for p in ((form or {}).get("m_PropInfo") or {}).get("m_Props", []):
        name, kind = p.get("m_Name"), p.get("m_Type")
        value = [p.get(f"m_DefValue[{i}]", 0.0) for i in range(4)]
        if kind in (2, 3):
            floats.setdefault(name, round(float(value[0]), 6))
        elif kind in (0, 1):
            colors.setdefault(name, [round(float(v), 6) for v in value])
        elif kind == 4:
            defaults[name] = ((p.get("m_DefTexture") or {}).get("m_DefaultName") or "white").lower()
    keywords = set(tree.get("m_ValidKeywords") or []) | set((tree.get("m_ShaderKeywords") or "").split())
    tex = {}
    for name, default in defaults.items():
        if name == "_MainTex":
            continue
        tex[name] = {**textures[name]} if name in textures else {"default": default}
    return {
        "program": program(shader),
        "shader": shader,
        **blend_state(form, floats),
        "straight": 1 if "_STRAIGHT_ALPHA_INPUT" in keywords else 0,
        "floats": floats,
        "colors": colors,
        "tex": tex,
    }


def skeleton(
    atlas_materials: Iterable[tuple[str | None, dict]],
    blend_modes: Mapping[str, Any],
    replacement: Callable[[dict], tuple[str | None, dict] | None],
    scale: float = 0.01,
) -> dict:
    """Describe a SkeletonDataAsset's page materials and blend-mode replacements.

    Args:
        atlas_materials: Atlas materials as (page texture name, material description) pairs.
        blend_modes: SkeletonDataAsset blendModeMaterials typetree controlling replacement lists.
        replacement: Resolves a material pointer to (page texture name, description), or None for a null reference.
        scale: Unity units per skeleton unit from the data asset.

    Returns:
        Ported-program pages, blend replacements by mode and page (None for unset references), and scale.
    """
    pages = {}
    for name, mat in atlas_materials:
        page = page_name(name)
        if page and mat.get("program") and page not in pages:
            pages[page] = mat
    blend: dict[str, dict[str, Any]] = {}
    if blend_modes.get("requiresBlendModeMaterials"):
        for mode, key in _BLEND_LISTS:
            if mode == "additive" and not blend_modes.get("applyAdditiveMaterial"):
                continue  # additive slots keep their page's material (and add through their vertex alpha 0)
            entries = blend_modes.get(key) or []
            if not entries:
                continue
            out = {}
            for entry in entries:
                found = (
                    replacement(entry.get("material") or {}) if (entry.get("material") or {}).get("m_PathID") else None
                )
                out[page_name(entry.get("pageName")) or ""] = found[1] if found else None
            blend[mode] = out
    return {"pages": pages, "blend": blend, "scale": round(float(scale), 6)}


def layer(
    resolve: Callable[[str, int, dict], tuple[str, Any] | None] | None, sa_pid: int, sa: Mapping[str, Any]
) -> dict:
    """Attach SkeletonAnimation material data to a lobby layer for skeleton-materials.js.

    Args:
        resolve: Reads a skeleton pointer by owner path ID; None leaves page and blend maps empty.
        sa_pid: SkeletonAnimation path ID owning the data-asset pointer.
        sa: SkeletonAnimation typetree supplying skeletonDataAsset and tintBlack.

    Returns:
        Materials with pages, blend, tintBlack, and scale in Unity units per skeleton unit
        (the noise programs' center units); unreadable data leaves empty page and blend maps.
    """
    pointer = sa.get("skeletonDataAsset") or {}
    found = resolve("skeleton", sa_pid, pointer) if resolve is not None and pointer.get("m_PathID") else None
    data = found[1] if found else {}
    return {
        "materials": {
            "pages": dict(data.get("pages") or {}),
            "blend": dict(data.get("blend") or {}),
            "scale": data.get("scale", 0.01),
            "tintBlack": bool(sa.get("tintBlack")),
        }
    }


def textures(layers: Iterable[Mapping[str, Any]]) -> dict[str, dict]:
    """Collect material textures beyond atlas pages as lobby.json fx.textures entries.

    Args:
        layers: Exported lobby layers with page and replacement materials; textures use particle-serving locators.
    """
    out = {}
    for l in layers:
        mats = l.get("materials") or {}
        found = list((mats.get("pages") or {}).values())
        for by_page in (mats.get("blend") or {}).values():
            found += [m for m in by_page.values() if m]
        for mat in found:
            for tex in (mat.get("tex") or {}).values():
                if "id" in tex:
                    out[tex["id"]] = dict(tex)
    return out
