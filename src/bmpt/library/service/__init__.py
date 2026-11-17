"""The mod library (``ModLibrary``), a package of one module per section of the class.

``library`` composes ``ModLibrary`` from the mixins of the other modules; ``base`` holds what they share. The package
re-exports the names other modules and the tests read from it.
"""

from bmpt.engine import t
from ...core.filelock import file_lock
from ..install import install_staged
from ..rebuild import rebuild
from ..storage import atomic_clone, atomic_copy, atomic_json, verified_move, verify_file
from .base import (
    RECOVERY_RETRY,
    _ChoiceChange,
    _LibraryLock,
    _View,
    _WornVerdicts,
    _background,
    _errors,
    _folder_size,
    _has_successor,
    _held_after,
    _item_fraction,
    _rebuilt,
)
from .library import ModLibrary

__all__ = [
    "_background",
    "_ChoiceChange",
    "_errors",
    "_folder_size",
    "_has_successor",
    "_held_after",
    "_item_fraction",
    "_LibraryLock",
    "_rebuilt",
    "_View",
    "_WornVerdicts",
    "atomic_clone",
    "atomic_copy",
    "atomic_json",
    "file_lock",
    "install_staged",
    "ModLibrary",
    "rebuild",
    "RECOVERY_RETRY",
    "t",
    "verified_move",
    "verify_file",
]
