"""The game build the App was tested on (BL-163).

The App ships, per server, the resource versions it was tested on (``discovery.resource_version``) and a short hash of
every official bundle name of the newest of them (``catalog/data/tested_build.json``, written by
``scripts/build_tested_build.py`` from the development view)::

    {"schema": 1,
     "servers": {"global": {"build": "<Steam build id>", "versions": ["<resource version>", ...],
                            "names": "<base64 of the sorted KEY_BYTES-byte name keys>"}}}

On an install of another resource version the build is untested: reads work as always, and a write of anything but the
official files onto a core the update changed (a core with a bundle whose name the tested build does not have: a part
rebuilt under a new CRC, or a new one) needs the user's confirmation. A bundle name carries its content's CRC, so the
names alone tell a changed core, without reading a file. A server without data (Japan today) is never untested.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "catalog" / "data" / "tested_build.json"
SCHEMA = 1
KEY_BYTES = 6  # 48 bits: an unlisted name matching a listed one by chance stays below one in a billion per update


def name_key(name: str) -> bytes:
    """The short key of a bundle name the data lists."""
    return hashlib.sha1(name.lower().encode("utf-8")).digest()[:KEY_BYTES]


@dataclass(frozen=True)
class Tested:
    """The tested build of one server."""

    server: str
    build: str
    versions: frozenset[str]
    keys: frozenset[bytes]

    def has(self, name: str) -> bool:
        """Whether the tested build has a bundle of this name."""
        return name_key(name) in self.keys


def bundle_names(dirs: Iterable[Path]) -> list[str]:
    """The bundle names of an install's resource folders, sorted, each once."""
    names: set[str] = set()
    for directory in dirs:
        with os.scandir(directory) as entries:
            names.update(entry.name for entry in entries
                         if entry.name.endswith(".bundle") and entry.is_file(follow_symlinks=False))
    return sorted(names)


def encode(build: str, versions: Iterable[str], names: Iterable[str]) -> dict:
    """One server's entry of the data file."""
    keys = sorted({name_key(name) for name in names})
    return {"build": str(build), "versions": list(dict.fromkeys(versions)),
            "names": base64.b64encode(b"".join(keys)).decode("ascii")}


def decode(data: dict) -> dict[str, Tested]:
    """The tested builds of a data file's content, by server; {} for another schema."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    out = {}
    for server, entry in (data.get("servers") or {}).items():
        raw = base64.b64decode(entry["names"])
        keys = frozenset(raw[i:i + KEY_BYTES] for i in range(0, len(raw), KEY_BYTES))
        out[server] = Tested(server, str(entry.get("build") or ""), frozenset(entry.get("versions") or ()), keys)
    return out


@functools.lru_cache(maxsize=1)
def _load(path: Path) -> dict[str, Tested]:
    try:
        return decode(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def for_server(server: str) -> Tested | None:
    """The tested build of a server, None when the App ships none for it."""
    return _load(FILE).get(server)
