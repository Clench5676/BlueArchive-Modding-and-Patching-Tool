"""What a 3D preview reads instead of the game files its own source does not supply.

A model export also loads bundles outside the previewed slot: shared shaders, and textures stored under another core
(Shiroko (Terror)'s ch0263 materials take theirs from the NPC en0002's bundle). Read as they are, those files make a
preview follow the mods switch and whatever else is in the game folder. ``PreviewReads`` names the bytes to read for
each of them instead (``ModLibrary.preview_reads``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class PreviewReads:
    """``resolve`` maps a game file the export would load (a prefab bundle or a bundle the CAB index finds) to the
    file to read instead. ``key`` names the rule and the state its choices depend on; it is part of the export's cache
    key. ``trusted`` admits the locations ``resolve`` may return outside the resource folders, so a persisted export
    that read them can be reused."""

    resolve: Callable[[Path], Path]
    key: str
    trusted: Callable[[Path], bool]
