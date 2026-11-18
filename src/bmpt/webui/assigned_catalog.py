"""Expose saved character ownership without inventing installed asset handles."""

from copy import deepcopy
from typing import Any, Iterable

from ..catalog.records import alt_resource_core


def students_with_assignments(catalog: Any, assignments: Iterable[str]) -> list:
    """Add the alts mods are assigned to, but the install lacks, to the installed student list.

    An alt added this way has the catalog's offline cross-server names and no asset handles. This gives a stored
    mod a place in the student list even when the game folder lacks its portrait, memorial lobby, or chibi.

    Args:
        catalog: The game catalog; ``students()`` is the installed student list, ``assignment_students()`` also lists
            students that are not installed.
        assignments: Keys of the alts that mods are assigned to.

    Returns:
        Copies of the students, sorted by key. In each student the alts are ordered: the student's own alt
        first, then released on the selected server, then playable, then by catalog order.
    """
    students = {student.key: deepcopy(student) for student in catalog.students()}
    present = {alt.key for student in students.values() for alt in student.alts}
    wanted = set(assignments) - present
    if not wanted:
        return list(students.values())
    for available in catalog.assignment_students():
        selected = [alt for alt in available.alts if alt.key in wanted]
        if not selected:
            continue
        if available.key not in students:
            student = deepcopy(available)
            student.alts = []
            students[student.key] = student
        students[available.key].alts.extend(selected)
    for student in students.values():
        student.alts.sort(
            key=lambda alt: (
                alt.key != student.key,
                not alt.released_on_server,
                not alt.playable,
                alt.order,
                alt.key,
            )
        )
    return [students[key] for key in sorted(students)]


def student_with_assignments(catalog: Any, assignments: Iterable[str], key: str) -> Any:
    """Find one student of the student list that ``students_with_assignments`` builds.

    Args:
        catalog: The game catalog.
        assignments: Keys of the alts that mods are assigned to.
        key: The student's key; case does not matter.

    Returns:
        The student; when the student list has none under ``key``, whatever ``catalog.student(key)`` returns or raises.
    """
    for student in students_with_assignments(catalog, assignments):
        if student.key == key.lower():
            return student
    return catalog.student(key)


def stored_asset_fields(alt: Any, mods: Iterable[Any]) -> dict:
    """Say which asset kinds an alt has mods for and which its install carries.

    The two lists differ for an alt that is assigned but not installed: its mods still have previews.

    Args:
        alt: The alt of the student list.
        mods: The library's mods; those whose ``target`` is this alt count.

    Returns:
        ``storedResourceTypes``: the sorted kinds (``portrait``, ``memorialLobby``, ``chibi``) among the alt's mods;
        ``localKinds``: the kinds the install has an asset for.
    """
    own = [mod for mod in mods if mod.target == alt.key]
    return {
        "storedResourceTypes": [
            resource_type
            for resource_type in ("memorialLobby", "chibi", "portrait")
            if any(mod.resource_type == resource_type for mod in own)
        ],
        "localResourceTypes": [
            resource_type
            for resource_type in ("portrait", "memorialLobby", "chibi")
            if alt_resource_core(alt, resource_type)
        ],
    }
