"""Renderers whose stills the Preview Cache keeps, and the files that decide their pixels.

A cached still stays valid only while the code that drew it is unchanged: the cache key of every persisted still
carries the content hash of its renderer's pixel files (``revision``), so a release that changes how a still is drawn
never serves a stale picture. Each pixel file's imports are either pixel files of the same renderer or listed in
``NOT_PIXELS`` (loading, scheduling, errors); ``tests/test_still_renderers.py`` holds every import to that.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..core.errors import ServiceError

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# paths below web/; vendor files by their versioned path (the import map and spine-runtime.js name them)
RENDERERS: dict[str, tuple[str, ...]] = {
    # the square 3D still of a Switch Chibi card (chibi-still.js) and the viewer that draws it
    "chibi": (
        "js/chibi/chibi-still.js",
        "js/chibi/chibi.js",
        "js/chibi/chibi-camera.js",
        "js/core/png-decode.js",
        "js/chibi/chibi-materials.js",
        # the halo's particle effects (chibi-fx.js) with the memorial lobby simulation and programs they run on
        "js/chibi/chibi-fx.js",
        "js/memorial-lobby/particles.js",
        "js/memorial-lobby/particles-geometry.js",
        "js/memorial-lobby/particles-gl.js",
        "js/app/touch-fx.js",
        "vendor/three-0.160.0/build/three.module.min.js",
        "vendor/three-0.160.0/examples/jsm/loaders/GLTFLoader.js",
        "vendor/three-0.160.0/examples/jsm/utils/BufferGeometryUtils.js",
    ),
    # portrait, memorial lobby and collection stills drawn with the Spine player (thumbs.js)
    "spine": (
        "js/detail/thumbs.js",
        "js/memorial-lobby/memorial-lobby-still.js",
        "js/memorial-lobby/memorial-lobby-health.js",
        "js/spine/rest-pose.js",
        "js/detail/special-view.js",
        "js/spine/spine-load.js",
        "js/spine/spine-runtime.js",
        "vendor/spine-player-4.2.120/spine-player.min.js",
    ),
}

# imports of pixel files that never change a still's pixels
NOT_PIXELS = frozenset(
    {
        "js/core/api.js",
        "js/core/async.js",
        "js/core/capture-priority.js",
        "js/core/capture-retries.js",
        "js/memorial-lobby/memorial-lobby-touch.js",
        "js/ui/ngui.js",
        "js/detail/preview-cache.js",
        "js/detail/preview-error.js",
        "js/detail/preview-lifecycle.js",
        "js/mods/switch-sources.js",
        "vendor/three-0.160.0/examples/jsm/controls/OrbitControls.js",
        # particles-gl.js's memorial lobby drawing only (create()): the 3D chibi view uses its shader sources alone
        "js/core/linear.js",
        "js/memorial-lobby/mesh-fx.js",
    }
)

_lock = threading.Lock()
_revisions: dict[tuple[str, str], str] = {}


def revision(renderer: object, root: Path = WEB_DIR) -> str:
    """Hash the pixel files of a renderer; each hash is computed once per root.

    Args:
        renderer: A key of ``RENDERERS``. Typed ``object`` because it comes from the page's request.
        root: The folder the pixel files are relative to.

    Returns:
        The hex digest of the renderer's name and its files' names and contents.

    Raises:
        ServiceError: ``bad_params``, the renderer is not a known one.
    """
    if not isinstance(renderer, str) or renderer not in RENDERERS:
        raise ServiceError("bad_params", key="renderer")
    with _lock:
        found = _revisions.get((str(root), renderer))
        if found is None:
            digest = hashlib.sha256(renderer.encode())
            for name in RENDERERS[renderer]:
                data = (root / name).read_bytes()
                digest.update(f"\n{name}\n{len(data)}\n".encode())
                digest.update(data)
            found = _revisions[(str(root), renderer)] = digest.hexdigest()
        return found
