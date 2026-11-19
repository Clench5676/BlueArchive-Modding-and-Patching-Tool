"""The fingerprint of the code that builds a mod's game files: part of every build key.

A built output (a mod adapted to another character, a mod of an older game release updated to the current parts)
is reused while what it was made from is unchanged. Whether the builder itself still makes the same bytes is told by
this fingerprint: a digest of the builder modules' code and of the versions of the packages that shape the bytes.
Comments, docstrings and layout do not count. The shipped value is ``catalog/data/builder_fingerprints.json``, written
by ``scripts/build_builder_fingerprints.py``; a packaged program has no sources to digest.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

#: the one kind of builder the library signs with a fingerprint (costume builds sign with their rule number)
BUILD = "build"

#: builder code, relative to ``src``: the engine and the App's own builders
MODULES = (
    "ba_modding_toolkit",
    "bmpt/library/alts.py",
    "bmpt/library/bundle_saves.py",
    "bmpt/library/install.py",
    "bmpt/library/rebuild.py",
    "bmpt/spine",
    "bmpt/unity/bundle_guard.py",
    "bmpt/unity/texture_guard.py",
)
#: folders of the engine that build nothing: its windows, its device tools and its command line
NOT_BUILDERS = ("ba_modding_toolkit/gui", "ba_modding_toolkit/adb", "ba_modding_toolkit/cli")

#: packages whose version changes the bytes a build writes
PACKAGES = ("unitypy", "pillow", "texture2ddecoder", "etcpak", "astc-encoder-py", "lz4", "brotli")

_DATA = Path(__file__).resolve().parents[1] / "catalog" / "data" / "builder_fingerprints.json"


def _code(source: str) -> str:
    """Give a module's code without docstrings, comments, layout and positions."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)


def _locked_versions(lock: str) -> dict[str, str]:
    found = dict(re.findall(r'^name = "([^"]+)"\nversion = "([^"]+)"', lock, flags=re.MULTILINE))
    return {name: found.get(name, "") for name in PACKAGES}


def compute(src: Path, lock: Path) -> dict[str, str]:
    """Digest the builder modules under a source tree and the locked package versions.

    Args:
        src: The checkout's ``src`` folder.
        lock: The checkout's ``uv.lock``.

    Returns:
        The data file's content: the fingerprint of each builder kind, 12 hexadecimal digits.
    """
    digest = hashlib.sha1()
    for entry in MODULES:
        path = src / entry
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            name = file.relative_to(src).as_posix()
            if name.startswith(tuple(f"{folder}/" for folder in NOT_BUILDERS)):
                continue
            digest.update(name.encode())
            digest.update(_code(file.read_text(encoding="utf-8")).encode())
    digest.update(json.dumps(_locked_versions(lock.read_text(encoding="utf-8")), sort_keys=True).encode())
    return {BUILD: digest.hexdigest()[:12]}


@lru_cache(maxsize=1)
def _shipped() -> dict[str, str]:
    return json.loads(_DATA.read_text(encoding="utf-8"))


def fingerprint(kind: str = BUILD) -> str:
    """Give the shipped fingerprint of a builder kind, as a build key's builder part."""
    return f"{kind}-{_shipped()[kind]}"
