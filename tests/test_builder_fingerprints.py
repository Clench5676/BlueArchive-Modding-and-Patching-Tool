"""Pin the shipped builder fingerprint to the builder sources."""

import json
from pathlib import Path

from bmpt.library import builders

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "bmpt" / "catalog" / "data" / "builder_fingerprints.json"


def test_shipped_fingerprint_matches_the_builder_sources():
    current = builders.compute(ROOT / "src", ROOT / "uv.lock")
    assert json.loads(DATA.read_text(encoding="utf-8")) == current, (
        "a builder module or package changed: run `uv run python scripts/build_builder_fingerprints.py --write`"
    )
    assert builders.fingerprint() == f"build-{current['build']}"


def test_every_listed_module_exists():
    assert all((ROOT / "src" / entry).exists() for entry in builders.MODULES)


def test_comments_and_layout_do_not_change_the_code_digest():
    plain = "def build(a):\n    return a + 1\n"
    dressed = '"""About."""\n\n\ndef build(a):\n    """Says what."""\n    # a note\n    return a  +  1\n'
    assert builders._code(plain) == builders._code(dressed)
    assert builders._code(plain) != builders._code(plain.replace("+ 1", "+ 2"))
