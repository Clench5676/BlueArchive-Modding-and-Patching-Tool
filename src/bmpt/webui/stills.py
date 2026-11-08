"""Renderers whose stills the Preview Cache keeps, and the files that decide their pixels.

A cached still stays valid only while the code that drew it is unchanged: the cache key of every persisted still
carries the content hash of its renderer's pixel files (``revision``), so a release that changes how a still is drawn
never serves the old picture. Each pixel file's imports are either pixel files of the same renderer or listed in
``NOT_PIXELS`` (loading, scheduling, errors); ``tests/test_still_renderers.py`` holds every import to that.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..errors import ServiceError

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# paths below web/; vendor files by their versioned path (the import map and spine-runtime.js name them)
RENDERERS: dict[str, tuple[str, ...]] = {
    # the square 3D still of a Switch 3D Model card (model-still.js) and the viewer that draws it
    "model": ("js/model-still.js", "js/model.js", "js/png-decode.js", "js/chibi-materials.js",
              "vendor/three-0.160.0/build/three.module.min.js",
              "vendor/three-0.160.0/examples/jsm/loaders/GLTFLoader.js",
              "vendor/three-0.160.0/examples/jsm/utils/BufferGeometryUtils.js"),
    # portrait, lobby and collection stills drawn with the Spine player (thumbs.js)
    "spine": ("js/thumbs.js", "js/lobby-still.js", "js/lobby-health.js", "js/rest-pose.js", "js/special-view.js",
              "js/spine-load.js", "js/spine-runtime.js", "vendor/spine-player-4.2.120/spine-player.min.js"),
}

# imports of pixel files that never change a still's pixels
NOT_PIXELS = frozenset({
    "js/api.js", "js/async.js", "js/capture-priority.js", "js/capture-retries.js", "js/lobby-touch.js",
    "js/ngui.js", "js/preview-cache.js", "js/preview-error.js", "js/preview-lifecycle.js", "js/switch-sources.js",
    "vendor/three-0.160.0/examples/jsm/controls/OrbitControls.js",
})

_lock = threading.Lock()
_revisions: dict[tuple[str, str], str] = {}


def revision(renderer: object, root: Path = WEB_DIR) -> str:
    """The content hash of ``renderer``'s pixel files (bad_params for an unknown renderer). Computed once per root."""
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
