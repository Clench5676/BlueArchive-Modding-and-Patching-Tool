"""Expose saved character ownership without inventing installed asset handles."""

from copy import deepcopy
from typing import Any, Iterable


def students_with_assignments(catalog: Any, assignments: Iterable[str]) -> list:
    """Add the alts mods are assigned to, but the install lacks, to the installed roster.

    An alt added this way has the catalog's offline cross-server names and no asset handle (no ``spr``, ``lobby``
    or ``model``), so a mod for a student the game folder does not carry yet still has a place in the roster.

    Args:
        catalog: The game catalog; ``families()`` is the installed roster, ``assignment_families()`` also lists
            students that are not installed.
        assignments: Keys of the alts that mods are assigned to.

    Returns:
        Copies of the students, sorted by key. In each student the alts are ordered: the student's own alt
        first, then recruitable, then playable, then by catalog order.
    """
    students = {student.key: deepcopy(student) for student in catalog.families()}
    present = {alt.key for student in students.values() for alt in student.variants}
    wanted = set(assignments) - present
    if not wanted:
        return list(students.values())
    for available in catalog.assignment_families():
        selected = [alt for alt in available.variants if alt.key in wanted]
        if not selected:
            continue
        if available.key not in students:
            student = deepcopy(available)
            student.variants = []
            students[student.key] = student
        students[available.key].variants.extend(selected)
    for student in students.values():
        student.variants.sort(
            key=lambda alt: (
                alt.key != student.key,
                not alt.recruitable,
                not alt.playable,
                alt.order,
                alt.key,
            )
        )
    return [students[key] for key in sorted(students)]


def student_with_assignments(catalog: Any, assignments: Iterable[str], key: str) -> Any:
    """Find one student of the roster that ``students_with_assignments`` builds.

    Args:
        catalog: The game catalog.
        assignments: Keys of the alts that mods are assigned to.
        key: The student's key; case does not matter.

    Returns:
        The student; when the roster has none under ``key``, whatever ``catalog.family(key)`` returns or raises.
    """
    for student in students_with_assignments(catalog, assignments):
        if student.key == key.lower():
            return student
    return catalog.family(key)


def stored_asset_fields(alt: Any, mods: Iterable[Any]) -> dict:
    """Say which asset kinds an alt has mods for and which its install carries.

    The two lists differ for an alt that is assigned but not installed: its mods still have previews.

    Args:
        alt: The alt of the roster.
        mods: The library's mods; those whose ``target`` is this alt count.

    Returns:
        ``storedKinds``: the sorted kinds (``spr``, ``lobby``, ``model``) among the alt's mods;
        ``localKinds``: the kinds the install has an asset for.
    """
    own = [mod for mod in mods if mod.target == alt.key]
    return {
        "storedKinds": sorted({mod.kind for mod in own if mod.kind in ("spr", "lobby", "model")}),
        "localKinds": [kind for kind in ("spr", "lobby", "model") if getattr(alt, kind, None)],
    }
