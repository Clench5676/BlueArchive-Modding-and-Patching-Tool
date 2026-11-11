"""Write catalog/data/tested_build.json: the game build the App was tested on (``bmpt.library.tested_build``).

Run it on an official, verified game folder of the build the test suite passed on, after a game update: it records
that folder's resource version and a short hash of each of its bundle names for the server, and keeps the other
servers' entries. No game file is read but the catalog hash the resource version digests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Support direct invocation from a source checkout without installing the script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ba_modding_toolkit.searching import get_search_dirs  # noqa: E402
from bmpt.gameassets.discovery import resource_version  # noqa: E402
from bmpt.library import tested_build  # noqa: E402


def entry(game: Path, build: str) -> dict:
    """The data file's entry for the game folder ``game``."""
    dirs = get_search_dirs(game)
    if not dirs:
        raise SystemExit(f"No resource folders under {game}")
    names = tested_build.bundle_names(dirs)
    if not names:
        raise SystemExit(f"No bundles under {game}")
    return tested_build.encode(build, [resource_version(dirs)], names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True, help="the game folder of the tested build")
    parser.add_argument("--build", required=True, help="its Steam build id, for the record")
    parser.add_argument("--server", default="global", choices=("global", "japan"))
    parser.add_argument("--out", default=str(tested_build.FILE), help="the file to update, - for standard output")
    args = parser.parse_args(argv)
    out = None if args.out == "-" else Path(args.out)
    try:
        data = json.loads((out or tested_build.FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    servers = dict(data.get("servers") or {}) if data.get("schema") == tested_build.SCHEMA else {}
    servers[args.server] = entry(args.game, args.build)
    text = json.dumps({"schema": tested_build.SCHEMA, "servers": dict(sorted(servers.items()))}, indent=1) + "\n"
    found = tested_build.decode(json.loads(text))[args.server]
    if out is None:
        sys.stdout.write(text)
    else:
        out.write_text(text, encoding="utf-8")
    print(f"{args.server}: build {found.build}, resource version {next(iter(found.versions))}, "
          f"{len(found.keys)} bundle names -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
