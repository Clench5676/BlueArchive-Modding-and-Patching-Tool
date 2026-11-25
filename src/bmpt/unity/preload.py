"""Keep UnityPy's container index from searching the disk.

Since UnityPy 1.25 the first read of ``Environment.container`` follows every pointer of each AssetBundle's preload
table, to give container paths to the objects a container entry depends on. A pointer into another bundle makes
UnityPy look for that bundle: when it is not loaded, ``Environment.find_file`` walks the whole folder the bundle was
read from, which is the game's folder of some 24,000 bundles, or the working directory for a bundle read from bytes.
Older releases did not follow these pointers, and nothing here needs paths for objects of other bundles, so pointers
into other files are left out of the table before UnityPy reads it; pointers inside the file are still followed.
"""

from __future__ import annotations

from UnityPy.helpers.ContainerHelper import ContainerHelper

_parse_preload_table = ContainerHelper.parse_preload_table


def _parse_local_preload_table(self: ContainerHelper) -> None:
    table = self._preload_table
    if table:
        # None keeps the indices of the container entries; UnityPy skips a false entry.
        self._preload_table = [pptr if pptr is not None and pptr.m_FileID == 0 else None for pptr in table]
    _parse_preload_table(self)


def keep_container_lookups_in_memory() -> None:
    """Install the local-only preload table on UnityPy's container helper; calling it again changes nothing."""
    if ContainerHelper.parse_preload_table is not _parse_local_preload_table:
        ContainerHelper.parse_preload_table = _parse_local_preload_table
