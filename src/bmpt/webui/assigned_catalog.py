"""Expose saved character ownership without inventing installed asset handles."""

from copy import deepcopy
from typing import Any, Iterable


def families_with_assignments(catalog: Any, assignments: Iterable[str]) -> list:
    """Add the variants mods are assigned to, but the install lacks, to the installed roster.

    A variant added this way has the catalog's offline cross-server names and no asset handle (no ``spr``, ``lobby``
    or ``model``), so a mod for a student the game folder does not carry yet still has a place in the roster.

    Args:
        catalog: The game catalog; ``families()`` is the installed roster, ``assignment_families()`` also lists
            students that are not installed.
        assignments: Keys of the variants that mods are assigned to.

    Returns:
        Copies of the families, sorted by key. In each family the variants are ordered: the family's own variant
        first, then recruitable, then playable, then by catalog order.
    """
    families = {family.key: deepcopy(family) for family in catalog.families()}
    present = {variant.key for family in families.values() for variant in family.variants}
    wanted = set(assignments) - present
    if not wanted:
        return list(families.values())
    for available in catalog.assignment_families():
        selected = [variant for variant in available.variants if variant.key in wanted]
        if not selected:
            continue
        if available.key not in families:
            family = deepcopy(available)
            family.variants = []
            families[family.key] = family
        families[available.key].variants.extend(selected)
    for family in families.values():
        family.variants.sort(
            key=lambda variant: (
                variant.key != family.key,
                not variant.recruitable,
                not variant.playable,
                variant.order,
                variant.key,
            )
        )
    return [families[key] for key in sorted(families)]


def family_with_assignments(catalog: Any, assignments: Iterable[str], key: str) -> Any:
    """Find one family of the roster that ``families_with_assignments`` builds.

    Args:
        catalog: The game catalog.
        assignments: Keys of the variants that mods are assigned to.
        key: The family's key; case does not matter.

    Returns:
        The family; when the roster has none under ``key``, whatever ``catalog.family(key)`` returns or raises.
    """
    for family in families_with_assignments(catalog, assignments):
        if family.key == key.lower():
            return family
    return catalog.family(key)


def stored_asset_fields(variant: Any, mods: Iterable[Any]) -> dict:
    """Say which asset kinds a variant has mods for and which its install carries.

    The two lists differ for a variant that is assigned but not installed: its mods still have previews.

    Args:
        variant: The variant of the roster.
        mods: The library's mods; those whose ``target`` is this variant count.

    Returns:
        ``storedKinds``: the sorted kinds (``spr``, ``lobby``, ``model``) among the variant's mods;
        ``localKinds``: the kinds the install has an asset for.
    """
    own = [mod for mod in mods if mod.target == variant.key]
    return {
        "storedKinds": sorted({mod.kind for mod in own if mod.kind in ("spr", "lobby", "model")}),
        "localKinds": [kind for kind in ("spr", "lobby", "model") if getattr(variant, kind, None)],
    }
