"""BL-134: the costume rule's version keys the saved verdicts and preview builds of costume pairs in place of the
program's; the rule's sources are pinned to it, so a change to them asks for a new version."""

import hashlib
import json
from pathlib import Path

import bmpt
from bmpt.gameassets.costume_rule import COSTUME_RULE, RULE_SOURCES

PINNED = Path(__file__).parent / "fixtures" / "costume_rule.json"


def test_the_costume_rule_sources_are_pinned_to_its_version():
    root = Path(bmpt.__file__).parent
    digest = hashlib.sha256(b"".join((root / name).read_bytes().replace(b"\r\n", b"\n") for name in RULE_SOURCES))
    pinned = json.loads(PINNED.read_text())
    assert (COSTUME_RULE, digest.hexdigest()) == (pinned["rule"], pinned["sha256"]), (
        "the costume build changed: bump COSTUME_RULE in bmpt/gameassets/costume_rule.py and pin the sources in "
        f"tests/fixtures/costume_rule.json (rule {COSTUME_RULE}, sha256 {digest.hexdigest()})")
