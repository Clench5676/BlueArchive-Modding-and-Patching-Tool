"""Write catalog/data/builder_fingerprints.json: the fingerprint of the code that builds game files.

Run it after changing a builder module or a package the builders use (``bmpt.library.builders`` lists both); the test
suite fails until the file matches the sources. A new fingerprint makes the program build its kept outputs again at
its next start.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Support direct invocation from a source checkout without installing the script.
sys.path.insert(0, str(ROOT / "src"))

from bmpt.library import builders  # noqa: E402


def main() -> None:
    """Print the data file, or write it with ``--write``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the data file instead of printing it")
    args = parser.parse_args()
    text = json.dumps(builders.compute(ROOT / "src", ROOT / "uv.lock"), indent=2, sort_keys=True) + "\n"
    if args.write:
        (ROOT / "src" / "bmpt" / "catalog" / "data" / "builder_fingerprints.json").write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
